"""Importes y el idioma de la hoja.

Google devuelve los valores formateados tal como se ven en pantalla, que
depende del idioma configurado en la hoja: en español un importe sale como
"-2,85". Y gspread, al convertirlo a número, borra las comas dando por hecho
que separan miles, así que ese importe se volvía -285: cien veces más.

La app pide los valores sin formatear precisamente para no depender de eso.
"""

import pandas as pd
import pytest

from conftest import FakeSpreadsheet, FakeWorksheet, data_constants

data = data_constants()


# ── La causa, documentada ─────────────────────────────────────────────────────

def test_gspread_borra_la_coma_decimal():
    """Deja constancia del comportamiento del que nos protegemos.

    Si algún día gspread deja de hacerlo, este test avisa y se puede
    reconsiderar la opción de renderizado.
    """
    from gspread.utils import numericise

    assert numericise("-2,85") == -285
    assert numericise("1,555") == 1555


# ── La app pide valores sin formatear ─────────────────────────────────────────

def test_las_transacciones_se_piden_sin_formatear(monkeypatch):
    recibido = {}

    class Espia(FakeWorksheet):
        def get_all_records(self, **kwargs):
            recibido.update(kwargs)
            return []

    monkeypatch.setattr(data, "_spreadsheet",
                        lambda: FakeSpreadsheet({"transactions": Espia(records=[])}))
    data.load_transactions.clear()
    data.load_transactions()

    assert recibido.get("value_render_option") == data._SIN_FORMATO


def test_las_posiciones_se_piden_sin_formatear(monkeypatch):
    recibido = {}

    class Espia(FakeWorksheet):
        def get_all_records(self, **kwargs):
            recibido.update(kwargs)
            return []

    monkeypatch.setattr(data, "_spreadsheet",
                        lambda: FakeSpreadsheet({"positions": Espia(records=[])}))
    data.load_positions.clear()
    data.load_positions()

    assert recibido.get("value_render_option") == data._SIN_FORMATO


def test_un_importe_sin_formatear_llega_intacto(monkeypatch):
    """El caso real: -2,85 € en la hoja debe seguir siendo -2.85 en la app."""
    fila = {c: "" for c in data._TX_COLUMNS}
    fila.update({"tx_id": "abc", "date": "2026-08-03", "datetime": "2026-08-03T10:00:00",
                 "amount": -2.85, "type": "card", "merchant_norm": "supermercado",
                 "event_domain": "cashflow", "tipus": "out", "rule_confidence": 1})

    monkeypatch.setattr(data, "_spreadsheet",
                        lambda: FakeSpreadsheet({"transactions": FakeWorksheet(records=[fila])}))
    data.load_transactions.clear()
    df = data.load_transactions()

    assert df["amount"].iloc[0] == pytest.approx(-2.85)


# ── El parseo de respaldo, para pestañas escritas a mano ──────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    (-2.85,        -2.85),     # ya es número: se respeta
    (0,             0.0),
    ("-2,85",      -2.85),     # decimal español
    ("-2.85",      -2.85),     # decimal inglés
    ("1.234,56",   1234.56),   # miles con punto, decimal con coma
    ("1,234.56",   1234.56),   # al revés
    ("1.234.567,89", 1234567.89),
    ("-2,85 €",    -2.85),     # con símbolo de moneda
    ("1 234,56",   1234.56),   # con espacio de miles
])
def test_a_numero_interpreta_los_dos_formatos(entrada, esperado):
    assert data._a_numero(entrada) == pytest.approx(esperado)


@pytest.mark.parametrize("entrada", ["", "   ", "n/d", None, True, "€"])
def test_a_numero_devuelve_none_si_no_es_numero(entrada):
    assert data._a_numero(entrada) is None


# ── El gráfico de categorías ──────────────────────────────────────────────────

def test_el_grafico_lee_los_meses_como_texto(monkeypatch):
    valores = [
        ["category", "2026-07", "2026-08"],
        ["Compra", -100.5, -2.85],
    ]
    monkeypatch.setattr(data, "_spreadsheet", lambda: FakeSpreadsheet(
        {"display_category_month": FakeWorksheet(values=valores)}))
    data.load_category_month.clear()
    df = data.load_category_month()

    assert len(df) == 2
    assert df[df["year_month"] == pd.Period("2026-08")]["amount"].iloc[0] == pytest.approx(-2.85)


def test_el_grafico_admite_meses_como_serial_de_excel(monkeypatch):
    """Si la tabla dinámica agrupa por fecha, sin formatear llegan seriales."""
    serial_agosto_2026 = 46235          # 2026-08-01
    valores = [
        ["category", serial_agosto_2026],
        ["Compra", -2.85],
    ]
    monkeypatch.setattr(data, "_spreadsheet", lambda: FakeSpreadsheet(
        {"display_category_month": FakeWorksheet(values=valores)}))
    data.load_category_month.clear()
    df = data.load_category_month()

    assert len(df) == 1
    assert df["year_month"].iloc[0] == pd.Period("2026-08")
    assert df["amount"].iloc[0] == pytest.approx(-2.85)
