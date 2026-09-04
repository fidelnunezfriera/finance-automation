#!/usr/bin/env python3
"""Compara modelos de predicción sobre las series del proyecto.

Aplica el mismo protocolo —validación de origen móvil, sin fuga— a cualquiera
de las series, para poder responder con números a «¿qué modelo uso?» en vez de
con intuición.

Uso:
    python pipeline/evaluar_series.py --serie aportaciones
    python pipeline/evaluar_series.py --serie gasto-total
    python pipeline/evaluar_series.py --serie gasto-categoria --categoria Compra
    python pipeline/evaluar_series.py --serie gasto-total --csv out/modelos.csv

El mes en curso se descarta siempre: va a medias y se lee como un desplome.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "app"))

from pipeline.evaluacion import evaluar, tabla_resumen, modelos  # noqa: E402


def _series_disponibles(categoria: str | None) -> dict[str, pd.Series]:
    import data  # se importa aquí para no pagar la conexión si sólo se pide --help

    tx = data.load_transactions()

    series = {
        "aportaciones": data.drop_incomplete_month(data.monthly_investments(tx)),
        # Misma construcción que el resto del proyecto: los meses sin gasto
        # valen 0 EUR y el mes en curso queda fuera.
        "gasto-total":  data.monthly_expenses(tx),
    }

    if categoria:
        gastos = tx[(tx["event_domain_l"] == "cashflow") & (tx["amount"] < 0)]
        if categoria not in set(gastos["category"].dropna()):
            disponibles = sorted(gastos["category"].dropna().unique())
            sys.exit(f"No hay gastos en la categoria {categoria!r}.\n"
                     f"Disponibles: {', '.join(map(str, disponibles))}")
        series["gasto-categoria"] = data.monthly_expenses(tx, categoria)

    return series


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serie", required=True,
                    choices=["aportaciones", "gasto-total", "gasto-categoria"])
    ap.add_argument("--categoria", help="requerido con --serie gasto-categoria")
    ap.add_argument("--horizontes", default="1,3,6,12",
                    help="meses vista a evaluar, separados por comas")
    ap.add_argument("--min-entrenamiento", type=int, default=12,
                    help="meses mínimos antes del primer origen de prueba")
    ap.add_argument("--csv", help="vuelca los resultados a un CSV")
    args = ap.parse_args()

    if args.serie == "gasto-categoria" and not args.categoria:
        ap.error("--serie gasto-categoria necesita --categoria")

    horizontes = [int(h) for h in args.horizontes.split(",")]
    serie = _series_disponibles(args.categoria)[args.serie]

    if len(serie) <= args.min_entrenamiento:
        sys.exit(f"La serie tiene {len(serie)} meses y el minimo de "
                 f"entrenamiento es {args.min_entrenamiento}. Baja "
                 f"--min-entrenamiento o elige otra serie.")

    # El catálogo se filtra por cuánta historia hay: con pocos meses, los
    # modelos que necesitan años ni se plantean.
    catalogo = modelos.catalogo_para(len(serie))

    etiqueta = args.serie + (f" [{args.categoria}]" if args.categoria else "")
    print(f"Serie: {etiqueta}")
    print(f"  {len(serie)} meses: {serie.index[0]} - {serie.index[-1]}")
    print(f"  media {serie.mean():,.0f}  desv {serie.std():,.0f}  "
          f"CV {serie.std() / serie.mean():.0%}" if serie.mean() else "")
    print(f"  modelos: {len(catalogo)}   horizontes: {horizontes}")
    print()

    resultados = evaluar(serie, catalogo, horizontes, args.min_entrenamiento)

    print("MASE por horizonte (1.00 = igual que la prediccion ingenua, "
          "menos es mejor)")
    print(tabla_resumen(resultados, "MASE").round(3).to_string())
    print()
    print("Detalle")
    print(resultados.round(2).to_string(index=False))

    print()
    print("Lectura  (comparacion pareada contra la prediccion ingenua)")
    for h in horizontes:
        del_h = resultados[resultados["horizonte"] == h]
        ingenua = del_h[del_h["modelo"] == "ingenua"]
        if del_h.empty or ingenua.empty:
            continue
        mae_ing = float(ingenua["MAE"].iloc[0])
        mejor = del_h.iloc[0]

        print(f"  h={h:>2}: ingenua {mae_ing:,.0f} EUR de MAE. "
              f"Mejor: '{mejor['modelo']}' con {mejor['MAE']:,.0f}")

        # Se listan solo los que mejoran de forma que no sea casualidad:
        # la diferencia pareada tiene que superar dos errores estandar.
        claros = del_h[(del_h["mejora"].notna())
                       & (del_h["mejora"] > 2 * del_h["mejora_ee"])]
        if claros.empty:
            print("        ningun modelo mejora a la ingenua de forma "
                  "estadisticamente clara")
        else:
            for _, r in claros.sort_values("mejora", ascending=False).iterrows():
                print(f"        {r['modelo']:<20} mejora {r['mejora']:>6,.0f} EUR "
                      f"(+-{r['mejora_ee']:,.0f})  {r['mejora'] / mae_ing:+.0%}")

    if args.csv:
        destino = Path(args.csv)
        destino.parent.mkdir(parents=True, exist_ok=True)
        resultados.to_csv(destino, index=False, encoding="utf-8")
        print(f"\nResultados en {destino}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
