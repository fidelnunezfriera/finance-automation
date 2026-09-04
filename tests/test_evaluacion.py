"""El banco de evaluación de modelos.

Lo que se comprueba aquí no es que los modelos acierten, sino que el
protocolo sea honesto: que ningún modelo llegue a ver el futuro, que el MASE
signifique lo que dice significar y que las series degeneradas no lo tumben.

Si el protocolo estuviera mal, todas las conclusiones que salgan de él lo
estarían, así que es la parte que más vale la pena fijar.
"""

import numpy as np
import pandas as pd
import pytest

from pipeline.evaluacion import escala_mase, evaluar, tabla_resumen
from pipeline.evaluacion import modelos as m


def _serie(valores, inicio="2020-01"):
    idx = pd.period_range(inicio, periods=len(valores), freq="M")
    return pd.Series([float(v) for v in valores], index=idx)


# ── Lo esencial: que no haya fuga ─────────────────────────────────────────────

def test_ningun_modelo_ve_mas_alla_de_su_origen():
    """El test que justifica todo lo demás.

    Un espía apunta cuántos puntos recibe en cada llamada. Si el protocolo
    filtrara futuro, alguna llamada traería más datos de los que existen
    antes del origen.
    """
    vistos = []

    def espia(y, h):
        vistos.append(len(y))
        return float(y[-1])

    serie = _serie(range(30))
    evaluar(serie, {"espia": espia}, horizontes=(1, 3), min_entrenamiento=12)

    assert vistos, "el modelo no llego a ejecutarse"
    assert min(vistos) == 12, "el primer origen debe entrenar con el minimo"
    assert max(vistos) <= len(serie) - 1, "nadie puede entrenar con la serie entera"


def test_el_modelo_recibe_exactamente_el_pasado():
    """No sólo la longitud: los valores tienen que ser los anteriores."""
    serie = _serie([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140])

    def comprobador(y, h):
        esperado = serie.values[:len(y)]
        assert np.array_equal(y, esperado), "el tramo de entrenamiento no cuadra"
        return float(y[-1])

    evaluar(serie, {"c": comprobador}, horizontes=(1,), min_entrenamiento=10)


# ── El MASE ───────────────────────────────────────────────────────────────────

def test_la_ingenua_da_mase_uno_a_un_mes():
    """Es la definición: el MASE mide contra la predicción ingenua."""
    rng = np.random.default_rng(0)
    serie = _serie(rng.normal(1000, 200, 60))

    r = evaluar(serie, {"ingenua": m.ingenua}, horizontes=(1,), min_entrenamiento=24)
    assert r["MASE"].iloc[0] == pytest.approx(1.0, abs=0.15)


def test_un_modelo_perfecto_da_mase_cero():
    serie = _serie([100 + 10 * i for i in range(40)])

    def perfecto(y, h):
        return float(y[-1] + 10 * h)

    r = evaluar(serie, {"perfecto": perfecto}, horizontes=(1, 3), min_entrenamiento=12)
    assert (r["MASE"] < 1e-9).all()


def test_una_serie_constante_deja_el_mase_indefinido():
    """Sin variación no hay vara de medir: mejor NaN que un infinito o un
    cero que parezca un acierto."""
    assert np.isnan(escala_mase(np.array([5.0, 5.0, 5.0])))


# ── Forma de los resultados ───────────────────────────────────────────────────

def test_los_horizontes_largos_tienen_menos_origenes():
    serie = _serie(range(40))
    r = evaluar(serie, {"ingenua": m.ingenua}, horizontes=(1, 12),
                min_entrenamiento=12)

    h1 = r[r["horizonte"] == 1]["origenes"].iloc[0]
    h12 = r[r["horizonte"] == 12]["origenes"].iloc[0]
    assert h12 == h1 - 11


def test_hay_una_fila_por_modelo_y_horizonte():
    serie = _serie(range(40))
    r = evaluar(serie, {"a": m.ingenua, "b": m.media_historica},
                horizontes=(1, 3, 6), min_entrenamiento=12)
    assert len(r) == 2 * 3


def test_la_tabla_resumen_pivota_bien():
    serie = _serie(range(40))
    r = evaluar(serie, {"a": m.ingenua, "b": m.media_historica},
                horizontes=(1, 6), min_entrenamiento=12)
    t = tabla_resumen(r, "MAE")

    assert list(t.columns) == [1, 6]
    assert set(t.index) == {"a", "b"}


def test_una_serie_demasiado_corta_avisa():
    with pytest.raises(ValueError, match="no queda nada"):
        evaluar(_serie(range(10)), {"a": m.ingenua}, min_entrenamiento=12)


# ── Los modelos ───────────────────────────────────────────────────────────────

def test_la_ingenua_repite_el_ultimo_valor():
    y = np.array([1.0, 2.0, 7.0])
    assert m.ingenua(y, 1) == 7.0
    assert m.ingenua(y, 5) == 7.0


def test_la_lineal_sigue_una_recta():
    """h=1 es el punto siguiente al ultimo visto, no el ultimo.

    La serie llega a 210; predecir a un mes son 220, y a tres meses 240.
    """
    y = np.array([100.0 + 10 * i for i in range(12)])   # 100 .. 210
    assert y[-1] == 210.0

    assert m.lineal_ponderada(6)(y, 1) == pytest.approx(220.0, abs=1)
    assert m.lineal_ponderada(6)(y, 3) == pytest.approx(240.0, abs=1)


def test_holt_amortiguado_frena_la_tendencia():
    """Lo que lo distingue del resto: proyectar no es prolongar la recta.

    Con phi < 1 la tendencia se apaga, así que a 12 meses queda por debajo de
    lo que daría una lineal. Es lo que evita convertir una meseta en una
    exponencial.
    """
    y = np.array([100.0 + 10 * i for i in range(24)])

    amortiguado = m.holt_amortiguado(phi=0.85)(y, 12)
    sin_amortiguar = m.holt_amortiguado(phi=1.0)(y, 12)

    assert amortiguado < sin_amortiguar


def test_el_exponencial_respeta_su_tope():
    """Aportaciones que se multiplican por diez cada mes: la tasa se recorta."""
    y = np.array([1.0 * (10 ** i) for i in range(8)])
    pred = m.exponencial(semivida=6, tope=0.02)(y, 1)
    assert pred <= y[-1] * 1.02 * 1.001


def test_el_exponencial_ignora_los_ceros_sin_reventar():
    y = np.array([100.0, 0.0, 120.0, 0.0, 140.0, 160.0])
    assert np.isfinite(m.exponencial(6)(y, 1))


def test_la_ingenua_estacional_mira_doce_meses_atras():
    y = np.array([float(i) for i in range(24)])
    assert m.ingenua_estacional(y, 1) == 12.0


def test_la_seleccion_interna_elige_la_variante_que_acierta():
    """Sobre una serie plana con ruido, la media gana a la extrapolación."""
    rng = np.random.default_rng(1)
    y = rng.normal(500, 30, 40)

    seleccion = m.con_seleccion_interna({
        "media": m.media_movil(6),
        "disparatada": lambda y, h: float(y[-1]) * 100,
    })
    assert seleccion(y, 1) == pytest.approx(y[-6:].mean(), abs=1)


def test_todos_los_modelos_del_catalogo_devuelven_un_numero():
    rng = np.random.default_rng(2)
    y = rng.gamma(2.0, 300.0, 40)

    for nombre, modelo in m.todos().items():
        for h in (1, 6):
            pred = modelo(y, h)
            assert np.isfinite(pred), f"{nombre} a h={h} devolvio {pred}"


# ── El catálogo se filtra por cuánta historia hay ─────────────────────────────

def test_con_muy_poca_historia_solo_compiten_los_modelos_simples():
    """Es lo que permite tener un catalogo grande sin que haga daño."""
    catalogo = m.catalogo_para(3)

    assert "ingenua" in catalogo
    assert "media historica" in catalogo
    for pesado in ("sarima", "holt-winters", "lightgbm", "ingenua estacional"):
        assert pesado not in catalogo, f"{pesado} no deberia entrar con 3 meses"


def test_con_dos_ciclos_entran_los_estacionales():
    catalogo = m.catalogo_para(24)

    for estacional in ("ingenua estacional", "holt-winters", "sarima"):
        assert estacional in catalogo
    assert "lightgbm" not in catalogo, "lightgbm necesita mas historia"


def test_con_historia_de_sobra_entran_todos():
    assert set(m.catalogo_para(120)) == set(m.CATALOGO)


def test_el_filtro_es_monotono():
    """Mas historia nunca puede quitar candidatos."""
    anterior: set[str] = set()
    for meses in (1, 3, 6, 12, 24, 36, 60, 120):
        actual = set(m.catalogo_para(meses))
        assert anterior <= actual, f"con {meses} meses se perdio algun modelo"
        anterior = actual


def test_todo_modelo_pertenece_a_una_familia():
    """Las familias sirven para combinar con diversidad: si un modelo se queda
    fuera, nunca podria ser elegido por esa via."""
    for nombre in m.CATALOGO:
        assert m.familia_de(nombre) != "otros", f"{nombre} no tiene familia"


def test_las_familias_no_se_solapan():
    vistos: set[str] = set()
    for miembros in m.FAMILIAS.values():
        repetidos = vistos & set(miembros)
        assert not repetidos, f"en dos familias a la vez: {repetidos}"
        vistos |= set(miembros)


# ── Los modelos nuevos ────────────────────────────────────────────────────────

def test_holt_winters_capta_la_estacionalidad():
    """Una serie que repite el mismo patron cada doce meses."""
    patron = [100, 90, 110, 95, 105, 100, 130, 80, 100, 95, 105, 120]
    y = np.array(patron * 5, dtype=float)

    pred = m.holt_winters()(y, 1)
    assert pred == pytest.approx(patron[0], rel=0.25)


def test_sarima_devuelve_algo_razonable():
    rng = np.random.default_rng(3)
    y = 500 + rng.normal(0, 40, 60)

    pred = m.sarima()(y, 1)
    assert 300 < pred < 700


def test_los_modelos_pesados_no_revientan_con_series_degeneradas():
    """Todo ceros, o casi: se cae a la media en vez de tumbar la evaluacion."""
    for y in (np.zeros(40), np.array([0.0] * 39 + [100.0])):
        for nombre in ("holt-winters", "sarima", "lightgbm"):
            modelo, _minimo = m.CATALOGO[nombre]
            assert np.isfinite(modelo(y, 1)), f"{nombre} no aguanta"


def test_una_prediccion_disparatada_se_cae_a_la_media():
    """SARIMA con raices explosivas llego a devolver 1e76 en datos reales.

    Un valor asi no solo es inutil: gana o pierde por goleada en la tabla y
    arrastra la combinacion si se cuela en el top-3.
    """
    y = np.array([100.0] * 20)

    assert m.saneado(lambda y, h: 1e76)(y, 1) == pytest.approx(100.0)
    assert m.saneado(lambda y, h: float("inf"))(y, 1) == pytest.approx(100.0)
    assert m.saneado(lambda y, h: float("nan"))(y, 1) == pytest.approx(100.0)
    assert m.saneado(lambda y, h: 1 / 0)(y, 1) == pytest.approx(100.0)


def test_la_red_de_seguridad_no_toca_lo_razonable():
    y = np.array([100.0] * 20)

    assert m.saneado(lambda y, h: 130.0)(y, 1) == pytest.approx(130.0)
    assert m.saneado(lambda y, h: 0.0)(y, 1) == pytest.approx(0.0)
    assert m.saneado(lambda y, h: -50.0)(y, 1) == pytest.approx(-50.0)


def test_todos_los_modelos_del_catalogo_van_saneados():
    """Cualquiera puede degenerar con una serie rara, no solo los sospechosos."""
    y = np.array([0.0] * 30 + [1e6])
    for nombre, (modelo, _minimo) in m.CATALOGO.items():
        pred = modelo(y, 12)
        assert np.isfinite(pred), f"{nombre} devolvio {pred}"
        assert abs(pred) <= 10 * (1e6 + 1), f"{nombre} se disparo a {pred}"


def test_lightgbm_aprende_un_patron_repetido():
    patron = [50, 50, 50, 300, 50, 50, 50, 50, 50, 50, 50, 50]
    y = np.array(patron * 6, dtype=float)

    # El mes 4 de cada año dispara: si aprende el mes del calendario, lo pilla.
    pred = m.lightgbm_lags()(y, 4)
    assert pred > 100, f"no ha aprendido el pico estacional: {pred}"


# ── La decisión guardada ──────────────────────────────────────────────────────

def _decision(modelos, horizontes=(1,), **extra):
    """Una decision como la que escribe el selector."""
    return {"series": {"s": {
        "meses": 75, "intermitente": False,
        "horizontes": {str(h): {"modelos": list(modelos), "origenes": 60,
                                "error_p10": -100.0, "error_p90": 200.0}
                       for h in horizontes},
        **extra,
    }}}


def test_sin_fichero_se_cae_al_modelo_por_defecto(tmp_path):
    """Una instalacion nueva no tiene decision, y aun asi tiene que predecir."""
    from pipeline.evaluacion import seleccion

    decision = seleccion.cargar(tmp_path / "no_existe.json")
    assert decision == {}

    p = seleccion.predecir(_serie([100.0, 200.0, 300.0, 400.0]), 1, decision, "s")

    assert np.isfinite(p.central)
    assert p.modelos == [seleccion.POR_DEFECTO]
    assert p.horizonte_validado is None


def test_un_fichero_corrupto_no_tumba_nada(tmp_path):
    from pipeline.evaluacion import seleccion

    ruta = tmp_path / "roto.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    assert seleccion.cargar(ruta) == {}


def test_predecir_combina_los_modelos_elegidos():
    """La prediccion es la media de los tres, no la del primero."""
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 10 + [500.0])
    p = seleccion.predecir(serie, 1, _decision(["ingenua", "media historica"]), "s")
    esperado = (m.ingenua(serie.values, 1) + m.media_historica(serie.values, 1)) / 2

    assert p.modelos == ["ingenua", "media historica"]
    assert p.central == pytest.approx(esperado)


def test_la_ingenua_entra_como_cualquier_otro_modelo():
    """Si es de los mejores se usa, sin tratamiento especial ni aviso."""
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 10 + [500.0])
    p = seleccion.predecir(serie, 1, _decision(["ingenua"]), "s")

    assert p.central == pytest.approx(500.0)
    assert "ingenua" in seleccion.explicacion(p)


def test_el_rango_sale_de_los_errores_observados():
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 10 + [500.0])
    p = seleccion.predecir(serie, 1, _decision(["ingenua"]), "s")

    assert p.bajo == pytest.approx(400.0)    # 500 + (-100)
    assert p.alto == pytest.approx(700.0)    # 500 + 200


def test_se_usa_el_mayor_horizonte_validado_que_no_pase_del_pedido():
    """Predecir a 18 meses con una decision tomada a 3 seria el error que
    todo esto trata de evitar: el ranking cambia con el horizonte."""
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 20)
    p = seleccion.predecir(serie, 18, _decision(["ingenua"], horizontes=(1, 3, 6, 12)), "s")

    assert p.horizonte_validado == 12
    assert p.horizonte_pedido == 18
    assert "orden de magnitud" in seleccion.explicacion(p)


def test_si_se_pide_menos_del_minimo_validado_se_usa_el_menor():
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 20)
    p = seleccion.predecir(serie, 1, _decision(["ingenua"], horizontes=(6, 12)), "s")
    assert p.horizonte_validado == 6


def test_una_serie_sin_decision_propia_usa_el_defecto():
    from pipeline.evaluacion import seleccion

    decision = {"series": {"otra": {"horizontes": {"1": {"modelos": ["ingenua"]}}}}}
    p = seleccion.predecir(_serie([100.0, 200.0, 300.0]), 1, decision, "s")

    assert p.modelos == [seleccion.POR_DEFECTO]


def test_un_modelo_desconocido_no_rompe_la_prediccion():
    """El JSON puede venir de una version con otro catalogo."""
    from pipeline.evaluacion import seleccion

    p = seleccion.predecir(_serie([100.0, 200.0, 300.0, 400.0]), 1,
                           _decision(["modelo_que_ya_no_existe"]), "s")
    assert np.isfinite(p.central)


def test_prever_un_periodo_suma_los_meses():
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 20, inicio="2025-01")     # termina en 2026-08
    meses = [pd.Period(f"2027-{k:02d}", freq="M") for k in range(1, 13)]
    p = seleccion.prever_periodo(serie, meses, _decision(["ingenua"], (1, 3, 6, 12)), "s")

    assert p.central == pytest.approx(1200.0)          # 12 x 100
    assert p.bajo < p.central < p.alto


def test_prever_un_periodo_ya_pasado_no_devuelve_nada():
    from pipeline.evaluacion import seleccion

    serie = _serie([100.0] * 20, inicio="2025-01")
    p = seleccion.prever_periodo(serie, [pd.Period("2025-03", freq="M")],
                                 _decision(["ingenua"]), "s")
    assert p.modelos == []


def test_la_explicacion_apunta_al_json():
    from pipeline.evaluacion import seleccion

    p = seleccion.predecir(_serie([100.0] * 20), 1, _decision(["ingenua"]), "s")
    texto = seleccion.explicacion(p)

    assert "modelos_elegidos.json" in texto
    assert "3 mejores" in texto or "mejores modelos" in texto


def test_los_horizontes_factibles_salen_de_los_origenes():
    """Sin umbrales a ojo: si no quedan origenes, ese horizonte no se evalua."""
    from pipeline.evaluacion import horizontes_factibles

    # 40 meses, 12 de entrenamiento, 12 origenes minimos -> hasta h=17
    assert horizontes_factibles(40, (1, 3, 6, 12, 24), 12, 12) == [1, 3, 6, 12]
    # 20 meses no dan ni para h=1 con esos minimos
    assert horizontes_factibles(20, (1, 3, 6, 12, 24), 12, 12) == []
    # Una serie corta puede calificar a corto plazo y no a largo
    assert horizontes_factibles(28, (1, 3, 6, 12, 24), 12, 12) == [1, 3]


def test_los_errores_por_origen_se_miden_uno_a_uno():
    from pipeline.evaluacion import errores_por_origen

    serie = _serie([100.0] * 20)
    errs = errores_por_origen(serie, m.ingenua, 1, min_entrenamiento=12)

    assert len(errs) == 8
    assert np.allclose(errs, 0.0)


def test_los_modelos_aguantan_una_serie_con_muchos_ceros():
    """Categorias de gasto intermitentes: casi todo ceros y algun pico."""
    y = np.array([0.0] * 20 + [300.0] + [0.0] * 8 + [250.0])

    for nombre, modelo in m.todos().items():
        assert np.isfinite(modelo(y, 1)), f"{nombre} no aguanta la serie dispersa"
