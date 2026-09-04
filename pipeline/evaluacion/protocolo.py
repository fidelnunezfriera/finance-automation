"""Comparación honesta de modelos sobre una serie temporal.

La partición no puede ser aleatoria: mezclar meses futuros en el entrenamiento
infla el acierto y no significa nada. Se usa **validación de origen móvil**,
que respeta el tiempo — entrenar con todo hasta el mes t, predecir t+h, avanzar
un mes y repetir.

Un modelo aquí es una función `(y_entrenamiento, h) -> prediccion`. Nada más.
Lo que reciba es todo lo que puede usar, y el protocolo garantiza que ahí sólo
hay pasado.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

Modelo = Callable[[np.ndarray, int], float]


def escala_mase(y_entrenamiento: np.ndarray) -> float:
    """Denominador del MASE: el error de la predicción ingenua en la muestra.

    Es la variación media de un mes al siguiente. Dividir por ella deja los
    errores en unidades interpretables: **MASE = 1 significa acertar tanto
    como decir «el mes que viene será como éste»**, por debajo de 1 el modelo
    aporta algo, y por encima no compensa la complejidad.
    """
    if len(y_entrenamiento) < 2:
        return float("nan")
    escala = float(np.mean(np.abs(np.diff(y_entrenamiento))))
    # Una serie constante da escala 0 y el MASE se iría a infinito. Se marca
    # como indefinido en vez de reventar o mentir con un cero.
    return escala if escala > 0 else float("nan")


def evaluar(
    serie: pd.Series,
    modelos: Mapping[str, Modelo],
    horizontes: Sequence[int] = (1, 3, 6, 12),
    min_entrenamiento: int = 12,
    referencia: str = "ingenua",
    max_origenes: int | None = None,
) -> pd.DataFrame:
    """Recorre la serie hacia delante comparando modelos en cada horizonte.

    En cada origen se entrena con `y[:t]` y se predice `t + h - 1`. Un modelo
    sólo se evalúa en los orígenes donde ese punto existe, así que los
    horizontes largos tienen menos observaciones — la columna `origenes` lo
    dice, y conviene mirarla antes de creerse una diferencia.

    Devuelve una fila por (modelo, horizonte) con MAE, RMSE y MASE.
    """
    y = serie.values.astype(float)
    n = len(y)
    if n <= min_entrenamiento:
        raise ValueError(
            f"La serie tiene {n} puntos y el mínimo de entrenamiento es "
            f"{min_entrenamiento}: no queda nada para probar.")

    errores: dict[tuple[str, int], list[float]] = {}
    escalas: dict[tuple[str, int], list[float]] = {}

    # Con `max_origenes` sólo se prueban los más recientes. Es lo que hace
    # asumible meter modelos caros —SARIMA se reajusta en cada origen— y
    # además responde mejor a la pregunta útil: qué funciona últimamente, no
    # qué funcionaba hace cinco años.
    primero = min_entrenamiento
    if max_origenes is not None and n - min_entrenamiento > max_origenes:
        primero = n - max_origenes

    for t in range(primero, n):
        y_train = y[:t]
        escala = escala_mase(y_train)

        for h in horizontes:
            objetivo = t + h - 1
            if objetivo >= n:
                continue
            real = y[objetivo]
            for nombre, modelo in modelos.items():
                pred = modelo(y_train, h)
                clave = (nombre, h)
                errores.setdefault(clave, []).append(abs(pred - real))
                escalas.setdefault(clave, []).append(escala)

    filas = []
    for (nombre, h), errs in errores.items():
        e = np.asarray(errs, dtype=float)
        esc = np.asarray(escalas[(nombre, h)], dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            mase = float(np.nanmean(e / esc)) if np.isfinite(esc).any() else float("nan")

        # Comparación PAREADA contra la referencia: todos los modelos se
        # evalúan sobre los mismos orígenes, así que restar error a error
        # cancela la dificultad de cada mes. Tratarlos como muestras
        # independientes infla la varianza y hace que todo parezca empatado.
        dif = dif_ee = float("nan")
        base = errores.get((referencia, h))
        if base is not None and nombre != referencia and len(base) == len(e):
            d = np.asarray(base, dtype=float) - e      # positivo = mejor que la referencia
            dif = float(d.mean())
            dif_ee = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float("nan")

        filas.append({
            "modelo":    nombre,
            "horizonte": h,
            "origenes":  len(e),
            "MAE":       float(e.mean()),
            "RMSE":      float(np.sqrt((e ** 2).mean())),
            "MASE":      mase,
            # Error estándar de la MEDIA, no desviación de los errores sueltos.
            # Es lo que dice si una diferencia entre modelos significa algo:
            # dos modelos separados por menos de ~2 errores estándar están
            # empatados por mucho que uno salga antes en la tabla.
            "MAE_ee":    (float(e.std(ddof=1) / np.sqrt(len(e)))
                          if len(e) > 1 else float("nan")),
            # Cuánto mejora a la referencia, en euros de MAE, y el error
            # estándar de esa diferencia. |dif| > 2*dif_ee es la regla de
            # bolsillo para decir que la mejora no es casualidad.
            "mejora":    dif,
            "mejora_ee": dif_ee,
        })

    return (pd.DataFrame(filas)
            .sort_values(["horizonte", "MAE"])
            .reset_index(drop=True))


def errores_por_origen(serie: pd.Series, modelo: Modelo, h: int,
                       min_entrenamiento: int = 12) -> np.ndarray:
    """Los errores con signo (real menos predicho) de un modelo, uno por origen.

    Sirven para construir intervalos con lo que de verdad pasó, en vez de
    suponer una distribución. Si el modelo se queda corto la mitad de las
    veces, el intervalo lo refleja sin que nadie lo modele.
    """
    y = serie.values.astype(float)
    n = len(y)
    errores = [y[t + h - 1] - modelo(y[:t], h)
               for t in range(min_entrenamiento, n)
               if t + h - 1 < n]
    return np.asarray(errores, dtype=float)


def horizontes_factibles(n_meses: int, escalera: Sequence[int],
                         min_entrenamiento: int, min_origenes: int) -> list[int]:
    """Hasta dónde llega una serie, en vez de un mínimo de meses inventado.

    Un horizonte es evaluable si, apartado el entrenamiento, quedan
    suficientes orígenes para que la medida signifique algo. Sale de la
    aritmética del protocolo, no de un umbral elegido a ojo: así una serie
    corta puede calificar a un mes y no a doce, que es exactamente lo que
    pasa en la realidad.
    """
    return [h for h in escalera
            if n_meses - min_entrenamiento - (h - 1) >= min_origenes]


def tabla_resumen(resultados: pd.DataFrame, metrica: str = "MASE") -> pd.DataFrame:
    """Pivota el resultado a modelos × horizontes, para leerlo de un vistazo."""
    return (resultados
            .pivot(index="modelo", columns="horizonte", values=metrica)
            .sort_values(by=resultados["horizonte"].min()))
