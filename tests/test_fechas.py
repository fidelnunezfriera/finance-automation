"""Conversión de las fechas que devuelve Google Sheets.

Sheets guarda las fechas como número de días desde 1899-12-30, y si la celda
lleva hora ese número tiene decimales. Un serial con decimales que se cuele sin
convertir lo interpreta pandas como nanosegundos desde 1970, así que las fechas
acaban en 1970-01-01 sin que salte ningún error.
"""

import pandas as pd
import pytest

from conftest import FakeWorksheet


def _convertir(data, valores):
    return list(data._fix_excel_serial(pd.Series(valores)))


def test_serial_entero_a_fecha(make_data):
    data = make_data({})
    assert _convertir(data, [46231])[0].startswith("2026-07-28")


def test_serial_con_hora_conserva_la_hora(make_data):
    """El caso que se iba a 1970: serial con parte decimal."""
    data = make_data({})

    resultado = _convertir(data, [46231.8102])[0]

    assert resultado.startswith("2026-07-28"), f"deberia ser 2026-07-28, es {resultado}"
    assert "19:26" in resultado, f"deberia conservar la hora, es {resultado}"


def test_un_serial_con_hora_no_acaba_en_1970(make_data):
    data = make_data({})

    convertido = _convertir(data, [46231.8102])[0]
    fecha = pd.to_datetime(convertido)

    assert fecha.year == 2026, f"cayo en {fecha.year}, deberia ser 2026"


def test_el_texto_de_fecha_se_respeta(make_data):
    data = make_data({})
    assert _convertir(data, ["2026-07-28"])[0] == "2026-07-28"


def test_los_vacios_se_respetan(make_data):
    """Una celda vacía no debe convertirse en una fecha inventada."""
    data = make_data({})

    resultado = _convertir(data, ["", None])

    assert resultado[0] == ""
    # pandas convierte None en NaN al construir la Serie; lo que importa es que
    # no salga de aquí como una fecha.
    assert pd.isna(resultado[1])


@pytest.mark.parametrize("fuera_de_rango", [0, 1, 99999, -5])
def test_los_numeros_fuera_de_rango_no_se_tocan(fuera_de_rango, make_data):
    """Sólo se convierte lo que puede ser una fecha plausible."""
    data = make_data({})
    assert _convertir(data, [fuera_de_rango])[0] == fuera_de_rango


def test_una_hoja_con_fechas_con_hora_se_carga_bien(make_data, tx_row):
    """Extremo a extremo: una hoja que guarda datetime como fecha-hora."""
    tabs = {
        "transactions": FakeWorksheet(records=[
            tx_row(tx_id="t1", date=46231, datetime=46231.8102, amount="-10"),
        ]),
        "positions": FakeWorksheet(records=[]),
        "display_category_month": FakeWorksheet(values=[]),
    }
    data = make_data(tabs)

    df = data.load_transactions()

    assert df["date"].iloc[0].year == 2026
    assert df["datetime"].iloc[0].year == 2026
    assert str(df["year_month"].iloc[0]) == "2026-07"
