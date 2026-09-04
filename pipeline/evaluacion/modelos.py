"""Catálogo de modelos candidatos.

Cada modelo es una función `(y, h) -> predicción a h pasos`. `y` es todo el
histórico disponible en ese momento y nada más: el protocolo se encarga de que
ahí no haya futuro.

No se usa ninguna dependencia nueva. Holt amortiguado son quince líneas de
numpy y meter `statsmodels` en el proyecto por eso encarecería la instalación,
que ya ha dado bastantes problemas.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np
from sklearn.linear_model import LinearRegression

# ── Utilidades ────────────────────────────────────────────────────────────────


def pesos_exponenciales(n: int, semivida: float) -> np.ndarray:
    """El último elemento pesa 1 y el peso se parte por dos cada `semivida`."""
    edad = np.arange(n - 1, -1, -1, dtype=float)
    return 0.5 ** (edad / float(semivida))


# ── Líneas base ───────────────────────────────────────────────────────────────


def ingenua(y: np.ndarray, h: int) -> float:
    """El mes que viene será como éste. Es la vara de medir del MASE."""
    return float(y[-1])


def ingenua_estacional(y: np.ndarray, h: int) -> float:
    """Lo mismo que hace un año. Sólo tiene sentido con estacionalidad anual."""
    if len(y) < 12:
        return float(y[-1])
    # Se busca el mismo mes del año anterior respecto al punto que se predice.
    idx = len(y) - 12 + (h - 1)
    return float(y[idx]) if 0 <= idx < len(y) else float(y[-12])


def media_historica(y: np.ndarray, h: int) -> float:
    return float(y.mean())


def media_movil(k: int) -> Callable[[np.ndarray, int], float]:
    def modelo(y: np.ndarray, h: int) -> float:
        return float(y[-k:].mean())
    return modelo


def media_ponderada(semivida: float) -> Callable[[np.ndarray, int], float]:
    def modelo(y: np.ndarray, h: int) -> float:
        return float(np.average(y, weights=pesos_exponenciales(len(y), semivida)))
    return modelo


# ── Regresiones ───────────────────────────────────────────────────────────────


def lineal_ponderada(semivida: float) -> Callable[[np.ndarray, int], float]:
    def modelo(y: np.ndarray, h: int) -> float:
        n = len(y)
        if n < 2:
            return float(y[-1])
        X = np.arange(n).reshape(-1, 1).astype(float)
        m = LinearRegression().fit(X, y, sample_weight=pesos_exponenciales(n, semivida))
        return float(m.predict([[n + h - 1]])[0])
    return modelo


def exponencial(semivida: float, tope: float = 0.10) -> Callable[[np.ndarray, int], float]:
    """Ajuste log-lineal: asume crecimiento porcentual constante.

    Es el modelo que había en el dashboard. Los meses a cero se descartan
    porque log(0) no existe, no porque no cuenten.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        n = len(y)
        pos = y > 0
        if pos.sum() < 2:
            return float(y.mean())
        X = np.arange(n).reshape(-1, 1).astype(float)
        w = pesos_exponenciales(n, semivida)
        m = LinearRegression().fit(X[pos], np.log(y[pos]), sample_weight=w[pos])
        tasa = float(np.clip(np.exp(m.coef_[0]) - 1, -tope, tope))
        ultimo = float(np.exp(m.predict([[n - 1]])[0]))
        return ultimo * (1 + tasa) ** h
    return modelo


def cuadratica_ponderada(semivida: float) -> Callable[[np.ndarray, int], float]:
    def modelo(y: np.ndarray, h: int) -> float:
        n = len(y)
        if n < 3:
            return float(y[-1])
        x = np.arange(n, dtype=float)
        X = np.column_stack([x, x ** 2])
        m = LinearRegression().fit(X, y, sample_weight=pesos_exponenciales(n, semivida))
        k = float(n + h - 1)
        return float(m.predict([[k, k ** 2]])[0])
    return modelo


# ── Suavizado exponencial ─────────────────────────────────────────────────────


def holt_amortiguado(alpha: float = 0.3, beta: float = 0.1,
                     phi: float = 0.9) -> Callable[[np.ndarray, int], float]:
    """Holt con tendencia amortiguada.

    Es el único del catálogo con una hipótesis sobre el proceso: hay nivel y
    hay tendencia, pero la tendencia **se apaga** al proyectar hacia delante
    (`phi < 1`). Por eso no convierte una meseta en una exponencial, que es
    justo lo que hacía el modelo log-lineal.

    Con phi = 1 es Holt clásico; con phi = 0, suavizado simple.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        n = len(y)
        if n < 2:
            return float(y[-1])
        nivel = float(y[0])
        tendencia = float(y[1] - y[0])
        for t in range(1, n):
            nivel_previo = nivel
            nivel = alpha * float(y[t]) + (1 - alpha) * (nivel + phi * tendencia)
            tendencia = beta * (nivel - nivel_previo) + (1 - beta) * phi * tendencia
        # La tendencia se suma amortiguada: phi + phi^2 + ... + phi^h
        amortiguacion = sum(phi ** i for i in range(1, h + 1))
        return float(nivel + amortiguacion * tendencia)
    return modelo


# ── Selección de hiperparámetros sin fuga ─────────────────────────────────────


def con_seleccion_interna(variantes: Mapping[str, Callable[[np.ndarray, int], float]],
                          min_entrenamiento: int = 8,
                          max_origenes: int = 18,
                          ) -> Callable[[np.ndarray, int], float]:
    """Elige la mejor variante **dentro** del entrenamiento, no mirando el test.

    Escoger la semivida o el tamaño de ventana sobre la serie completa es una
    fuga sutil: son decisiones tomadas con datos que luego se usan para
    evaluar. Aquí, en cada origen, se hace una validación interna sobre el
    tramo de entrenamiento y se elige con eso.

    `max_origenes` acota esa validación a los orígenes más recientes. Es lo
    que hace el coste manejable —recorrer todos los orígenes en cada origen es
    cuadrático y disparaba el tiempo de 0,3 a 12 segundos por serie— y además
    responde mejor a la pregunta: qué variante funciona *últimamente*.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        n = len(y)
        if n <= min_entrenamiento + h:
            # No hay sitio para validar por dentro: se usa la primera variante.
            return next(iter(variantes.values()))(y, h)

        origenes = range(max(min_entrenamiento, n - h + 1 - max_origenes),
                         n - h + 1)

        mejor, mejor_error = None, float("inf")
        for nombre, variante in variantes.items():
            errs = [abs(variante(y[:t], h) - y[t + h - 1]) for t in origenes]
            error = float(np.mean(errs)) if errs else float("inf")
            if error < mejor_error:
                mejor, mejor_error = variante, error

        return mejor(y, h)
    return modelo


# ── Estacionales y estadísticos clásicos ──────────────────────────────────────


def holt_winters(estacional: str = "add") -> Callable[[np.ndarray, int], float]:
    """Holt-Winters: nivel, tendencia amortiguada y estacionalidad anual.

    Necesita dos ciclos completos para estimar los doce factores estacionales,
    de ahí que su mínimo sea 24 meses. Con menos, `statsmodels` ni lo intenta.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        try:
            ajuste = ExponentialSmoothing(
                y, trend="add", damped_trend=True,
                seasonal=estacional, seasonal_periods=12,
                initialization_method="estimated",
            ).fit(optimized=True)
            return float(ajuste.forecast(h)[-1])
        except Exception:
            # Series degeneradas —todo ceros, o casi— hacen fallar el ajuste.
            # Devolver la media es preferible a tumbar la evaluación entera.
            return float(y.mean())
    return modelo


def sarima(orden=(1, 0, 1), estacional=(1, 0, 1, 12)
           ) -> Callable[[np.ndarray, int], float]:
    """SARIMA con estacionalidad anual.

    Los órdenes van fijos a propósito: buscarlos en cada origen multiplicaría
    el coste por veinte y, con estas longitudes, el buscador elige sobre todo
    ruido. (1,0,1)(1,0,1,12) es una configuración conservadora y habitual.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        try:
            # `enforce_stationarity` va activado a propósito. Desactivarlo hace
            # que el ajuste falle menos, pero admite raíces fuera del círculo
            # unidad y entonces la extrapolación explota: en esta misma serie
            # llegó a devolver 1e76 a doce meses. Mejor que falle el ajuste y
            # se caiga a la media.
            ajuste = SARIMAX(y, order=orden, seasonal_order=estacional,
                             enforce_stationarity=True,
                             enforce_invertibility=True).fit(disp=False)
            return float(ajuste.forecast(h)[-1])
        except Exception:
            return float(y.mean())
    return modelo


# ── Gradient boosting ─────────────────────────────────────────────────────────


def lightgbm_lags(n_lags: int = 12) -> Callable[[np.ndarray, int], float]:
    """LightGBM sobre retardos y mes del año.

    Aprende de los últimos `n_lags` meses y del mes del calendario. Es un
    modelo por serie: predice a `h` meses saltando directo, sin encadenar
    predicciones sobre predicciones, que acumularía el error.

    Necesita bastante historia porque cada ejemplo de entrenamiento consume
    `n_lags` meses: con 36 meses y 12 retardos quedan ~24 filas, que ya es el
    mínimo para que el árbol aprenda algo y no memorice.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        import lightgbm as lgb

        n = len(y)
        X, objetivo = [], []
        for t in range(n_lags, n - h + 1):
            X.append(list(y[t - n_lags:t]) + [(t + h - 1) % 12])
            objetivo.append(y[t + h - 1])
        if len(X) < 12:
            return float(y[-6:].mean())

        try:
            reg = lgb.LGBMRegressor(
                n_estimators=200, learning_rate=0.05, num_leaves=7,
                min_child_samples=5, verbose=-1, force_col_wise=True)
            reg.fit(np.asarray(X), np.asarray(objetivo))
            ultimo = np.asarray([list(y[n - n_lags:]) + [(n + h - 1) % 12]])
            return float(reg.predict(ultimo)[0])
        except Exception:
            return float(y[-6:].mean())
    return modelo


# ── Catálogo ──────────────────────────────────────────────────────────────────
#
# Cada modelo declara cuánta historia necesita. Eso es lo que permite tener un
# catálogo grande sin que haga daño: con tres meses de datos sólo compiten los
# cuatro modelos que pueden decir algo, y con ocho años entran todos. La regla
# de «a los 24 meses toca SARIMA» deja de ser un supuesto y pasa a ser una
# condición de admisión; quién gana lo sigue decidiendo la evaluación.
# ---------------------------------------------------------------------------

def saneado(f: Callable[[np.ndarray, int], float], factor: float = 10.0
            ) -> Callable[[np.ndarray, int], float]:
    """Red de seguridad: una predicción disparatada se cae a la media.

    No es paranoia. SARIMA con raíces explosivas llegó a devolver 1e76 en una
    serie de gasto real, y un valor así no sólo es inútil: gana o pierde por
    goleada en la tabla y arrastra la combinación si se cuela en el top-3.

    Se rechaza lo no finito y lo que se sale diez veces del rango observado.
    Un gasto mensual diez veces por encima de todo lo visto no es una
    predicción, es un fallo numérico.
    """
    def modelo(y: np.ndarray, h: int) -> float:
        try:
            pred = float(f(y, h))
        except Exception:
            return float(np.mean(y))
        if not np.isfinite(pred):
            return float(np.mean(y))
        techo = factor * (float(np.max(np.abs(y))) + 1.0)
        return pred if abs(pred) <= techo else float(np.mean(y))
    return modelo


_CRUDO: dict[str, tuple[Callable[[np.ndarray, int], float], int]] = {
    # nombre                    (modelo, meses mínimos)
    "ingenua":            (ingenua, 1),
    "media historica":    (media_historica, 1),
    "media movil 6":      (media_movil(6), 3),
    "media ponderada":    (con_seleccion_interna(
        {f"sv{sv}": media_ponderada(sv) for sv in (3, 6, 12)}), 3),
    "lineal ponderada":   (con_seleccion_interna(
        {f"sv{sv}": lineal_ponderada(sv) for sv in (3, 6, 12)}), 6),
    "cuadratica":         (cuadratica_ponderada(6), 12),
    "exponencial":        (exponencial(6), 6),
    "holt amortiguado":   (con_seleccion_interna(
        {f"phi{phi}": holt_amortiguado(phi=phi) for phi in (0.8, 0.9, 0.98)}), 6),
    "ingenua estacional": (ingenua_estacional, 24),
    "holt-winters":       (holt_winters(), 24),
    "sarima":             (sarima(), 24),
    "lightgbm":           (lightgbm_lags(), 36),
}

# Todos pasan por la red de seguridad, no sólo los sospechosos: cualquier
# modelo puede degenerar con una serie rara, y el banco tiene que aguantarlo.
CATALOGO: dict[str, tuple[Callable[[np.ndarray, int], float], int]] = {
    nombre: (saneado(modelo), minimo) for nombre, (modelo, minimo) in _CRUDO.items()
}

# Las familias son para combinar con diversidad: promediar tres modelos que
# cometen el mismo error no cancela nada.
FAMILIAS: dict[str, tuple[str, ...]] = {
    "nivel":      ("ingenua", "media historica", "media movil 6", "media ponderada"),
    "tendencia":  ("lineal ponderada", "cuadratica", "exponencial", "holt amortiguado"),
    "estacional": ("ingenua estacional", "holt-winters", "sarima"),
    "aprendizaje": ("lightgbm",),
}


def catalogo_para(n_meses: int) -> dict[str, Callable[[np.ndarray, int], float]]:
    """Los modelos que esa cantidad de historia admite."""
    return {nombre: modelo for nombre, (modelo, minimo) in CATALOGO.items()
            if n_meses >= minimo}


def familia_de(nombre: str) -> str:
    for familia, miembros in FAMILIAS.items():
        if nombre in miembros:
            return familia
    return "otros"


def todos() -> dict[str, Callable[[np.ndarray, int], float]]:
    """El catálogo entero, sin filtrar. Para resolver nombres guardados."""
    return {nombre: modelo for nombre, (modelo, _) in CATALOGO.items()}
