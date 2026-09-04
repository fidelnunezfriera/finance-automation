"""La ventana de meses sobre la que se calculan media y tendencia.

`monthly_investments` sólo devuelve meses con movimiento, así que un hueco de
tres años ocupa lo mismo que un mes: dos filas seguidas. Ajustar sobre eso hace
que una venta suelta de 2021 pese igual que la aportación del mes pasado, y
arrastra tanto la recta del gráfico como la media que se usa por defecto en las
proyecciones.

`contribution_window` acota al último año cerrado y rellena con ceros los meses
sin inversión, que son un dato y no un hueco.
"""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

from conftest import data_constants

data = data_constants()

HOY = pd.Timestamp("2026-08-03")


def _serie(pares):
    idx = pd.PeriodIndex([p for p, _ in pares], freq="M")
    return pd.Series([v for _, v in pares], index=idx, name="invested")


def _mensual(valores, inicio="2024-08"):
    """Una serie a partir de valores sueltos, con meses consecutivos."""
    idx = pd.period_range(inicio, periods=len(valores), freq="M")
    return pd.Series([float(v) for v in valores], index=idx, name="invested")


def _pendiente(s):
    y = s.values.astype(float)
    X = np.arange(len(y)).reshape(-1, 1).astype(float)
    return float(LinearRegression().fit(X, y).coef_[0])


# ── El mes en curso ───────────────────────────────────────────────────────────

def test_quita_el_mes_en_curso():
    """El dia 3 de agosto, agosto lleva tres dias de aportaciones."""
    serie = _serie([("2026-06", 700.0), ("2026-07", 760.0), ("2026-08", 12.0)])
    assert list(data.drop_incomplete_month(serie, hoy=HOY).index.astype(str)) \
        == ["2026-06", "2026-07"]


def test_no_quita_nada_si_el_ultimo_mes_ya_termino():
    serie = _serie([("2026-05", 700.0), ("2026-06", 760.0), ("2026-07", 800.0)])
    assert data.drop_incomplete_month(serie, hoy=HOY).equals(serie)


def test_si_solo_hay_mes_en_curso_se_conserva():
    """Mejor un dato parcial que ninguno."""
    serie = _serie([("2026-08", 12.0)])
    assert data.drop_incomplete_month(serie, hoy=HOY).equals(serie)


def test_un_mes_en_curso_flojo_ya_no_vuelve_negativa_la_tendencia():
    serie = _serie([
        ("2026-03", 586.0), ("2026-04", 615.0), ("2026-05", 176.0),
        ("2026-06", 606.0), ("2026-07", 612.0),
        ("2026-08", 12.0),          # tres dias de mes
    ])
    assert _pendiente(serie) < 0
    assert _pendiente(data.contribution_window(serie, hoy=HOY)) > 0


# ── La ventana ────────────────────────────────────────────────────────────────

def test_solo_entran_los_ultimos_meses_cerrados():
    serie = _serie([(f"2025-{m:02d}", 100.0) for m in range(1, 13)]
                   + [(f"2026-{m:02d}", 200.0) for m in range(1, 8)])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert list(ventana.index.astype(str))[0] == "2025-08"
    assert list(ventana.index.astype(str))[-1] == "2026-07"
    assert len(ventana) == 12


# ── Pesos exponenciales ───────────────────────────────────────────────────────

def test_el_mes_mas_reciente_pesa_uno():
    w = data.exponential_weights(12, semivida=4)
    assert w[-1] == pytest.approx(1.0)


def test_el_peso_se_parte_por_dos_cada_semivida():
    w = data.exponential_weights(13, semivida=4)
    assert w[-5] == pytest.approx(0.5)      # 4 meses atras
    assert w[-9] == pytest.approx(0.25)     # 8 meses atras
    assert w[-13] == pytest.approx(0.125)   # 12 meses atras


def test_los_pesos_decrecen_hacia_atras():
    w = data.exponential_weights(24)
    assert all(w[i] < w[i + 1] for i in range(len(w) - 1))


def test_una_semivida_mayor_aplana_los_pesos():
    corta = data.exponential_weights(24, semivida=3)
    larga = data.exponential_weights(24, semivida=12)
    assert larga[0] > corta[0], "con semivida larga, lo antiguo pesa mas"


def test_la_media_ponderada_se_acerca_a_lo_reciente():
    """Es el efecto que se busca: 24 meses de historia, nivel actual."""
    serie = _serie([(f"2025-{m:02d}", 200.0) for m in range(8, 13)]
                   + [(f"2026-{m:02d}", 800.0) for m in range(1, 8)])
    y = serie.values.astype(float)

    plana = y.mean()
    ponderada = float(np.average(y, weights=data.exponential_weights(len(y), 4)))
    reciente = 800.0

    assert plana == pytest.approx(550.0, abs=1)
    assert ponderada > plana
    assert abs(ponderada - reciente) < abs(plana - reciente), \
        "la ponderada debe quedar mas cerca del nivel actual que la plana"


def test_una_regresion_ponderada_sigue_al_tramo_reciente():
    """Aportaciones planas y luego crecientes: sin pesos la pendiente se
    diluye con la parte plana; con pesos, sigue a lo de ahora."""
    serie = _serie([(f"2025-{m:02d}", 200.0) for m in range(1, 13)]
                   + [("2026-01", 400.0), ("2026-02", 600.0), ("2026-03", 800.0)])
    y = serie.values.astype(float)
    X = np.arange(len(y)).reshape(-1, 1).astype(float)

    sin_pesos = float(LinearRegression().fit(X, y).coef_[0])
    con_pesos = float(LinearRegression().fit(
        X, y, sample_weight=data.exponential_weights(len(y), 4)).coef_[0])

    assert con_pesos > sin_pesos


def test_lo_anterior_a_la_ventana_desaparece():
    """Una venta de hace anos ya no puede tocar la tendencia."""
    serie = _serie([
        ("2021-12", -58.30),
        ("2026-05", 176.0), ("2026-06", 606.0), ("2026-07", 612.0),
    ])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)
    assert -58.30 not in ventana.values


# ── Meses sin inversión ───────────────────────────────────────────────────────

def test_los_meses_sin_inversion_valen_cero():
    serie = _serie([("2026-04", 700.0), ("2026-07", 800.0)])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert list(ventana.index.astype(str)) == \
        ["2026-04", "2026-05", "2026-06", "2026-07"]
    assert ventana.loc[pd.Period("2026-05")] == 0.0
    assert ventana.loc[pd.Period("2026-06")] == 0.0


def test_los_vacios_iniciales_de_la_ventana_no_cuentan():
    """Si empezaste a invertir a mitad de la ventana, los meses de antes no
    son aportaciones de 0: es que aun no invertias. Dejarlos haria que la
    recta subiera desde cero y fingiera una tendencia disparada."""
    serie = _serie([("2026-05", 700.0), ("2026-06", 750.0), ("2026-07", 800.0)])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert list(ventana.index.astype(str)) == ["2026-05", "2026-06", "2026-07"]
    assert len(ventana) == 3


def test_un_mes_con_actividad_pero_neto_cero_se_conserva():
    """Comprar y vender lo mismo deja 0 EUR netos, pero es un mes con
    actividad: no es un vacio inicial."""
    serie = _serie([("2026-05", 0.0), ("2026-06", 750.0), ("2026-07", 800.0)])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert list(ventana.index.astype(str)) == ["2026-05", "2026-06", "2026-07"]


def test_dejar_de_invertir_se_nota():
    """La ventana se ancla en el ultimo mes cerrado, no en la ultima compra."""
    serie = _serie([("2026-01", 700.0), ("2026-02", 700.0), ("2026-03", 700.0)])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert list(ventana.index.astype(str))[-1] == "2026-07"
    assert ventana.tail(4).eq(0).all()
    assert _pendiente(ventana) < 0


# ── Guardas ───────────────────────────────────────────────────────────────────

def test_serie_vacia():
    assert data.contribution_window(_serie([]), hoy=HOY).empty


def test_una_ventana_entera_sin_actividad_cae_a_lo_ultimo_que_hubo():
    """Alguien que dejo de invertir hace anos: una serie de ceros no dice
    nada, sus ultimas aportaciones si."""
    serie = _serie([("2021-10", 100.0), ("2021-11", 200.0), ("2021-12", 300.0)])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert list(ventana.index.astype(str)) == ["2021-10", "2021-11", "2021-12"]


def test_el_tamano_de_la_ventana_es_configurable():
    serie = _serie([(f"2026-{m:02d}", 100.0) for m in range(1, 8)])
    assert len(data.contribution_window(serie, meses=3, hoy=HOY)) == 3
    assert len(data.contribution_window(serie, meses=6, hoy=HOY)) == 6


# ── El tope de crecimiento de la proyección ───────────────────────────────────

def test_la_tasa_mensual_se_lee_en_anual():
    """+3,858% mensual no se lee como +57% anual en la cabeza de nadie."""
    assert data.tasa_anual(0.03858) == pytest.approx(0.575, abs=0.01)
    assert data.tasa_anual(0.02) == pytest.approx(0.268, abs=0.005)
    assert data.tasa_anual(0.0) == pytest.approx(0.0)
    assert data.tasa_anual(-0.02) == pytest.approx(-0.215, abs=0.005)


def test_quien_acota_la_proyeccion_es_la_amortiguacion_no_un_tope():
    """Ya no hay recorte a la tasa: cualquier valor converge a una meseta.

    Incluso un 10% mensual, que sin amortiguar multiplicaba la aportacion por
    92.000 a diez años.
    """
    for g in (0.02, 0.10, 0.50):
        con_amortiguacion = data.aportaciones_proyectadas(760, g, 360)[-1]
        assert np.isfinite(con_amortiguacion)
        assert con_amortiguacion == pytest.approx(
            data.meseta_aportacion(760, g), rel=0.01)

    sin_amortiguar = data.aportaciones_proyectadas(760, 0.10, 120,
                                                   persistencia=1.0)[-1]
    assert sin_amortiguar / 760 > 90_000


def test_la_persistencia_se_estima_de_la_serie():
    """Es lo que sustituye al 0,97 elegido a dedo.

    Una serie plana no tiene crecimiento que persista, asi que la
    persistencia sale baja y la meseta se queda donde esta.
    """
    plana = _mensual([750.0] * 24)
    p = data.estimar_persistencia(plana)

    assert p < 0.9, f"una serie plana no deberia dar persistencia {p}"


def test_un_crecimiento_sostenido_da_persistencia_alta():
    """Si de verdad creces mes a mes, el modelo no debe apagarlo."""
    creciente = _mensual([300 * 1.05 ** i for i in range(24)])
    assert data.estimar_persistencia(creciente) >= 0.95


def test_un_escalon_no_se_confunde_con_crecimiento():
    """El caso real: se sube de ritmo una vez y se sostiene.

    El ajuste log-lineal lo lee como crecimiento compuesto, pero la
    persistencia estimada lo desmiente y la meseta cae cerca del nivel actual.
    """
    escalon = _mensual([250.0] * 12 + [750.0] * 12)
    p = data.estimar_persistencia(escalon)
    tasa = data._tasa_log_lineal(escalon.values.astype(float))
    meseta = data.meseta_aportacion(750, tasa, p)

    assert tasa > 0.03, "el ajuste log-lineal ve crecimiento donde hay un escalon"
    assert p <= 0.6, f"un escalon no es crecimiento persistente (p={p})"
    assert meseta < 750 * 1.5, "la meseta deberia quedarse cerca del nivel actual"


def test_una_serie_muy_corta_no_intenta_estimar():
    corta = _mensual([100.0, 200.0, 300.0])
    assert data.estimar_persistencia(corta) == data.PERSISTENCIA_CRECIMIENTO


def test_la_amortiguacion_acota_la_forma_pero_no_el_nivel():
    """El matiz que hay que tener presente al haber quitado el tope.

    La meseta escala con la tasa, asi que una tasa disparatada sigue dando una
    meseta disparatada. Lo que protege de eso es enseñarla, no recortarla.
    """
    assert data.meseta_aportacion(760, 0.02) / 760 == pytest.approx(1.9, abs=0.1)
    assert data.meseta_aportacion(760, 0.10) / 760 > 20


# ── El crecimiento amortiguado de las aportaciones ────────────────────────────

def test_los_incrementos_se_van_reduciendo():
    """Es la diferencia con una tasa fija: cada mes sube menos que el anterior.

    Con tasa fija compuesta, cada subida es mayor que la anterior para
    siempre. Nadie aporta asi: se sube de nivel una vez y se sostiene.
    """
    serie = data.aportaciones_proyectadas(760, 0.02, 60)
    incrementos = np.diff(serie)

    assert all(incrementos > 0), "deberia seguir creciendo"
    assert all(np.diff(incrementos) < 0), "cada incremento debe ser menor"


def test_una_tasa_fija_acelera_en_vez_de_frenar():
    """El comportamiento anterior, para contraste."""
    serie = data.aportaciones_proyectadas(760, 0.02, 60, persistencia=1.0)
    incrementos = np.diff(serie)

    assert all(np.diff(incrementos) > 0), "sin amortiguar, cada subida es mayor"


def test_la_aportacion_tiende_a_una_meseta():
    a_10 = data.aportaciones_proyectadas(760, 0.02, 120)[-1]
    a_30 = data.aportaciones_proyectadas(760, 0.02, 360)[-1]
    meseta = data.meseta_aportacion(760, 0.02)

    assert a_10 < a_30 <= meseta * 1.001
    assert a_30 == pytest.approx(meseta, rel=0.01), "a 30 anios ya esta en la meseta"


def test_la_meseta_es_finita_y_razonable():
    """Con tasa fija, a 30 anios la aportacion se iba a 948.000 EUR/mes."""
    meseta = data.meseta_aportacion(760, 0.02)
    sin_amortiguar = 760 * 1.02 ** 360

    assert meseta < 5_000
    assert sin_amortiguar > 900_000


def test_sin_crecimiento_la_aportacion_es_plana():
    serie = data.aportaciones_proyectadas(760, 0.0, 24)
    assert np.allclose(serie, 760.0)


def test_una_tasa_negativa_reduce_la_aportacion_hasta_un_suelo():
    """Baja, pero tampoco hasta cero: converge igual que hacia arriba."""
    serie = data.aportaciones_proyectadas(760, -0.02, 360)
    suelo = data.meseta_aportacion(760, -0.02)

    assert all(np.diff(serie) < 0)
    assert serie[-1] == pytest.approx(suelo, rel=0.01)
    assert suelo > 0


def test_persistencia_uno_recupera_el_comportamiento_anterior():
    serie = data.aportaciones_proyectadas(100, 0.01, 12, persistencia=1.0)
    assert serie[-1] == pytest.approx(100 * 1.01 ** 12)
    assert data.meseta_aportacion(100, 0.01, persistencia=1.0) == float("inf")


@pytest.mark.parametrize("meses", [0, -3])
def test_un_horizonte_vacio_no_revienta(meses):
    assert len(data.aportaciones_proyectadas(760, 0.02, meses)) == 0


# ── El efecto que motivó todo esto ────────────────────────────────────────────

def test_el_movimiento_viejo_ya_no_arrastra_la_media():
    serie = _serie([
        ("2021-12", -58.30),
        ("2026-02", 610.40), ("2026-03", 585.90), ("2026-04", 615.25),
        ("2026-05", 175.60), ("2026-06", 605.80), ("2026-07", 612.10),
    ])
    ventana = data.contribution_window(serie, meses=12, hoy=HOY)

    assert serie.mean() == pytest.approx(449.54, abs=0.5)
    assert ventana.mean() == pytest.approx(534.17, abs=0.5)
    assert ventana.mean() > serie.mean()
