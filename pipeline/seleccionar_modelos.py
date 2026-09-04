#!/usr/bin/env python3
"""Decide qué modelos usar para predecir cada serie, y deja constancia.

Corre el banco de evaluación sobre el gasto total, cada categoría y cada
subcategoría, y guarda la decisión en `logs/modelos_elegidos.json`, que es lo
que consume el dashboard. Predecir no cuesta nada; decidir sí, y por eso se
decide aquí y no al abrir una página.

**No elige el ganador, combina los tres mejores.** Partiendo una serie por la
mitad se comprobó que el ganador de la primera acababa el séptimo de ocho en
la segunda: cuando las diferencias caen dentro del ruido, quedarse con el
primero de la tabla es sobreajustar la validación. Si la predicción ingenua es
de los tres mejores, entra como cualquier otra; no tiene nada de especial.

Nada de umbrales elegidos a ojo. Qué series entran y hasta qué horizonte sale
de la aritmética del protocolo: hace falta un mínimo de orígenes de prueba
para que una medida signifique algo. Una serie corta puede calificar a un mes
y no a doce, y eso queda registrado.

Uso:
    python pipeline/seleccionar_modelos.py
    python pipeline/seleccionar_modelos.py --forzar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "app"))

from pipeline.evaluacion import (errores_por_origen, evaluar,  # noqa: E402
                                 horizontes_factibles, modelos as M)

DESTINO = _ROOT / "logs" / "modelos_elegidos.json"

N_COMBINAR = 3
ESCALERA = (1, 3, 6, 12, 24)
MIN_ORIGENES = 12        # orígenes de prueba mínimos para fiarse de una medida
MIN_ENTRENAMIENTO = 12   # meses antes del primer origen
# Tope de orígenes por evaluación. SARIMA se reajusta en cada uno, así que sin
# tope el catálogo completo sobre 25 series se va a más de media hora. Con 36
# baja a unos diez minutos y se sigue midiendo sobre tres años de pruebas.
MAX_ORIGENES = 36
# Por debajo de esta proporción de meses con gasto la serie es intermitente:
# casi todo ceros con algún pico. Se marca, no se descarta — el que consulta
# decide qué hacer con esa información.
UMBRAL_INTERMITENCIA = 0.5


def _huella(serie: pd.Series) -> str:
    """Identifica el contenido de la serie, no su longitud.

    Contar meses no basta: al regenerar las reglas de categorización, el
    histórico se reclasifica y las series cambian sin que avance el
    calendario. Con una huella del contenido, cualquier recategorización
    dispara la reevaluación sola.
    """
    crudo = np.round(serie.values.astype(float), 2).tobytes() + str(serie.index[0]).encode()
    return hashlib.sha1(crudo).hexdigest()[:16]


def _series(tx) -> dict[str, pd.Series]:
    """Todas las series candidatas. Quién califica se decide después."""
    import data

    series = {"gasto-total": data.monthly_expenses(tx)}

    gastos = tx[(tx["event_domain_l"] == "cashflow") & (tx["amount"] < 0)]
    if gastos.empty:
        return {}

    for categoria in sorted(gastos["category"].dropna().unique()):
        if str(categoria).strip():
            series[f"categoria:{categoria}"] = data.monthly_expenses(tx, categoria)

    for sub in sorted(gastos["subcategory"].dropna().unique()):
        if str(sub).strip():
            series[f"subcategoria:{sub}"] = data.monthly_expenses(
                tx, subcategoria=str(sub))

    return series


def _decidir(serie: pd.Series) -> dict | None:
    """Evalúa una serie en cada horizonte que admita. None si no admite ninguno."""
    n = len(serie)
    factibles = horizontes_factibles(n, ESCALERA, MIN_ENTRENAMIENTO, MIN_ORIGENES)
    if not factibles:
        return None

    catalogo = M.catalogo_para(n)
    resultados = evaluar(serie, catalogo, horizontes=factibles,
                         min_entrenamiento=MIN_ENTRENAMIENTO,
                         max_origenes=MAX_ORIGENES)

    con_gasto = int((serie > 0).sum())
    salida = {
        "meses":            n,
        "desde":            str(serie.index[0]),
        "hasta":            str(serie.index[-1]),
        "huella":           _huella(serie),
        "meses_con_gasto":  con_gasto,
        "intermitente":     bool(con_gasto / n < UMBRAL_INTERMITENCIA),
        "horizontes":       {},
    }

    for h in factibles:
        del_h = resultados[resultados["horizonte"] == h].head(N_COMBINAR)
        elegidos = del_h["modelo"].tolist()

        # El intervalo se construye con los errores que cometió LA COMBINACION,
        # no los de cada modelo por separado: es la combinación lo que se va a
        # usar, así que es su error el que hay que medir.
        funciones = [catalogo[nombre] for nombre in elegidos]

        def combinada(y, hh, fs=funciones):
            return float(np.mean([f(y, hh) for f in fs]))

        errs = errores_por_origen(serie, combinada, h, MIN_ENTRENAMIENTO)
        ingenua = resultados[(resultados["horizonte"] == h)
                             & (resultados["modelo"] == "ingenua")]

        salida["horizontes"][str(h)] = {
            "modelos":     elegidos,
            "mae":         [round(v, 2) for v in del_h["MAE"]],
            "origenes":    int(del_h["origenes"].iloc[0]),
            "mae_ingenua": round(float(ingenua["MAE"].iloc[0]), 2) if len(ingenua) else None,
            # Percentiles del error observado (real menos predicho). Sirven
            # para dar un rango medido en vez de suponer una distribución.
            "error_p10":   round(float(np.percentile(errs, 10)), 2) if len(errs) else None,
            "error_p50":   round(float(np.percentile(errs, 50)), 2) if len(errs) else None,
            "error_p90":   round(float(np.percentile(errs, 90)), 2) if len(errs) else None,
        }

    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forzar", action="store_true",
                    help="reevalua aunque las series no hayan cambiado")
    ap.add_argument("--destino", default=str(DESTINO))
    args = ap.parse_args()

    import data
    tx = data.load_transactions()
    series = {k: v for k, v in _series(tx).items() if len(v)}
    if not series:
        print("No hay gastos de los que aprender todavia.")
        return 0

    destino = Path(args.destino)
    huellas = {k: _huella(v) for k, v in series.items()}

    if destino.exists() and not args.forzar:
        try:
            previo = json.loads(destino.read_text(encoding="utf-8"))
            antes = {k: v.get("huella") for k, v in previo.get("series", {}).items()}
            if antes == huellas:
                print(f"Las series no han cambiado desde "
                      f"{previo.get('generado', '?')[:10]}. Nada que reevaluar.")
                print(f"Usa --forzar para rehacerlo. Fichero: {destino}")
                return 0
        except (json.JSONDecodeError, KeyError, TypeError):
            print("El fichero anterior no se puede leer; se rehace.")

    print(f"Evaluando {len(series)} series. Escalera de horizontes: "
          f"{', '.join(map(str, ESCALERA))} meses.\n")

    decisiones, descartadas = {}, []
    for nombre, serie in sorted(series.items()):
        decision = _decidir(serie)
        if decision is None:
            descartadas.append((nombre, len(serie)))
            continue
        decisiones[nombre] = decision
        hs = ", ".join(decision["horizontes"])
        marca = "  [intermitente]" if decision["intermitente"] else ""
        print(f"  {nombre:<34} {len(serie):>3} meses  ->  h = {hs}{marca}")

    if not decisiones:
        print("Ninguna serie tiene historia suficiente para evaluar nada.")
        return 0

    salida = {
        "generado":     datetime.now().isoformat(timespec="seconds"),
        "escalera":     list(ESCALERA),
        "combinacion":  f"media de los {N_COMBINAR} mejores",
        "min_origenes": MIN_ORIGENES,
        "series":       decisiones,
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(salida, indent=2, ensure_ascii=False),
                       encoding="utf-8")

    print(f"\n{len(decisiones)} series decididas. Guardado en {destino}")
    if descartadas:
        print(f"\n{len(descartadas)} sin historia suficiente para ningun horizonte:")
        for nombre, n in descartadas:
            print(f"  {nombre:<34} {n:>3} meses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
