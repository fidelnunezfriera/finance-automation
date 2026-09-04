"""Lee la decisión guardada y predice con ella.

Es lo que consume `logs/modelos_elegidos.json`. El dashboard no sabe nada del
banco de evaluación: pide una predicción y le llega, con la decisión ya tomada
por el pipeline.

La predicción es siempre **la media de los tres modelos elegidos** para esa
serie y ese horizonte. Si la predicción ingenua está entre los tres, entra
como cualquier otra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import modelos as M

POR_DEFECTO = "media ponderada"


@dataclass
class Prevision:
    """Una predicción, con lo que hace falta para juzgarla."""
    central: float
    bajo: float
    alto: float
    modelos: list[str] = field(default_factory=list)
    horizonte_pedido: int = 0
    horizonte_validado: int | None = None   # None = no habia decision
    meses_historia: int = 0
    intermitente: bool = False


def cargar(ruta: str | Path) -> dict:
    """La decisión guardada, o un diccionario vacío si no hay o no se lee."""
    ruta = Path(ruta)
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _entrada(decision: dict, nombre_serie: str) -> dict | None:
    return (decision or {}).get("series", {}).get(nombre_serie)


def _horizonte_aplicable(entrada: dict, h: int) -> str | None:
    """El mayor horizonte validado que no pase del pedido.

    Predecir a 18 meses con una decisión tomada a 3 sería el error que todo
    esto trata de evitar: el ranking de modelos cambia con el horizonte. Si
    sólo se validó hasta 12, se usa 12 y se dice.
    """
    validados = sorted(int(k) for k in entrada.get("horizontes", {}))
    if not validados:
        return None
    aplicables = [v for v in validados if v <= h]
    return str(aplicables[-1] if aplicables else validados[0])


def predecir(serie: pd.Series, h: int, decision: dict | None = None,
             nombre_serie: str = "") -> Prevision:
    """Predice a `h` meses combinando los modelos elegidos para esa serie."""
    y = serie.values.astype(float)
    if len(y) == 0:
        return Prevision(0.0, 0.0, 0.0)

    entrada = _entrada(decision or {}, nombre_serie)
    catalogo = M.todos()

    if not entrada:
        central = catalogo[POR_DEFECTO](y, h)
        return Prevision(central, central, central, [POR_DEFECTO], h, None, len(y))

    clave = _horizonte_aplicable(entrada, h)
    datos = entrada["horizontes"][clave]
    elegidos = [n for n in datos["modelos"] if n in catalogo] or [POR_DEFECTO]

    central = float(np.mean([catalogo[n](y, h) for n in elegidos]))

    # El rango sale de los errores que cometio esta misma combinacion en la
    # validacion: real menos predicho, asi que se suman al centro.
    p10, p90 = datos.get("error_p10"), datos.get("error_p90")
    bajo = central + p10 if p10 is not None else central
    alto = central + p90 if p90 is not None else central

    return Prevision(
        central=central,
        bajo=min(bajo, alto),
        alto=max(bajo, alto),
        modelos=elegidos,
        horizonte_pedido=h,
        horizonte_validado=int(clave),
        meses_historia=int(entrada.get("meses", len(y))),
        intermitente=bool(entrada.get("intermitente", False)),
    )


def prever_periodo(serie: pd.Series, meses: list[pd.Period],
                   decision: dict | None = None,
                   nombre_serie: str = "") -> Prevision:
    """Suma la previsión de varios meses — un año, un trimestre.

    Los rangos se suman también. Es conservador: da por hecho que los errores
    van todos en la misma dirección, cuando en la práctica se compensan en
    parte. Preferible a quedarse corto en un intervalo.
    """
    if serie.empty or not meses:
        return Prevision(0.0, 0.0, 0.0)

    ultimo = serie.index[-1]
    partes = [predecir(serie, int((mes - ultimo).n), decision, nombre_serie)
              for mes in meses if mes > ultimo]
    if not partes:
        return Prevision(0.0, 0.0, 0.0)

    primera = partes[0]
    return Prevision(
        central=sum(p.central for p in partes),
        bajo=sum(p.bajo for p in partes),
        alto=sum(p.alto for p in partes),
        modelos=primera.modelos,
        horizonte_pedido=max(p.horizonte_pedido for p in partes),
        horizonte_validado=max((p.horizonte_validado or 0) for p in partes) or None,
        meses_historia=primera.meses_historia,
        intermitente=primera.intermitente,
    )


def explicacion(prevision: Prevision, decision: dict | None = None) -> str:
    """El texto de la 'i' de informacion."""
    if not prevision.modelos:
        return "No hay datos suficientes para predecir esta serie."

    if prevision.horizonte_validado is None:
        return (f"Sin decision guardada para esta serie: se usa "
                f"'{POR_DEFECTO}'. Ejecuta `scripts\\seleccionar_modelos.bat` "
                f"para elegir modelos con tus datos.")

    lineas = [
        f"Media de los {len(prevision.modelos)} mejores modelos de entre los "
        f"que evaluamos: {', '.join(prevision.modelos)}.",
        "",
        f"Elegidos sobre {prevision.meses_historia} meses de historia, "
        f"validados a {prevision.horizonte_validado} meses vista.",
    ]
    if prevision.horizonte_validado < prevision.horizonte_pedido:
        lineas.append(
            f"La prediccion va a {prevision.horizonte_pedido} meses, mas alla "
            f"de donde llega la validacion: tomala como orden de magnitud.")
    if prevision.intermitente:
        lineas.append("Esta serie tiene muchos meses sin gasto, asi que la "
                      "prediccion es especialmente ruidosa.")
    lineas += ["", "El detalle de la evaluacion esta en "
               "`logs/modelos_elegidos.json`."]
    return "\n".join(lineas)
