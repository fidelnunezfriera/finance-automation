"""Carga de datos frente a hojas vacías, incompletas o mal configuradas.

Estos casos son los que se encuentra alguien que acaba de montar el proyecto:
la hoja existe pero está recién creada, le falta una pestaña, o las cabeceras
no coinciden. Antes reventaban con un KeyError críptico.
"""

import pandas as pd
import pytest

from conftest import FakeWorksheet, data_constants

TX_COLUMNS = data_constants()._TX_COLUMNS
POS_COLUMNS = data_constants()._POS_COLUMNS


def _empty_sheet():
    """Hoja recién creada: pestañas con cabecera pero sin filas."""
    return {
        "transactions": FakeWorksheet(records=[]),
        "positions": FakeWorksheet(records=[]),
        "display_category_month": FakeWorksheet(values=[]),
    }


# ── Hoja vacía ────────────────────────────────────────────────────────────────

def test_transacciones_vacias_conservan_el_esquema(make_data):
    data = make_data(_empty_sheet())
    df = data.load_transactions()

    assert df.empty
    for col in TX_COLUMNS:
        assert col in df.columns, f"falta la columna {col}"


def test_posiciones_vacias_conservan_el_esquema(make_data):
    data = make_data(_empty_sheet())
    df = data.load_positions()

    assert df.empty
    assert "quantity" in df.columns


def test_categorias_vacias_devuelven_formato_largo(make_data):
    data = make_data(_empty_sheet())
    df = data.load_category_month()

    assert df.empty
    assert list(df.columns) == ["category", "year_month", "amount"]


def test_el_dashboard_arranca_con_la_hoja_vacia(make_data, monkeypatch):
    """El fallo original: el dashboard moría antes de pintar nada."""
    import importlib.util
    import sys

    from conftest import ROOT

    make_data(_empty_sheet())  # deja el doble en sys.modules["data"]

    spec = importlib.util.spec_from_file_location("_dash", ROOT / "app" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() se ejecuta al importar


# ── Hoja con datos ────────────────────────────────────────────────────────────

def test_las_transacciones_se_parsean(make_data, tx_row):
    tabs = _empty_sheet()
    tabs["transactions"] = FakeWorksheet(
        records=[
            tx_row(tx_id="t1", date="2026-01-15", amount="-42.50"),
            tx_row(tx_id="t2", date="2026-02-03", amount="1200"),
        ]
    )
    data = make_data(tabs)
    df = data.load_transactions()

    assert len(df) == 2
    assert df["amount"].tolist() == [-42.50, 1200.0]
    assert str(df["year_month"].iloc[0]) == "2026-01"
    assert df["tipus_norm"].iloc[0] == "GASTO"


def test_datetime_se_normaliza_a_utc_aware(make_data, tx_row):
    """El fallo original: tx_row trae 'datetime' sin zona horaria
    ("2026-01-15 10:00:00"). Sin normalizar, comparar esa columna contra un
    cutoff tz-aware (como hace la página Dashboard con los KPIs de 30 días)
    reventaba con TypeError: Invalid comparison between dtype=datetime64[us]
    and Timestamp -- no un dato mal calculado, un crash de la página entera.
    """
    tabs = _empty_sheet()
    tabs["transactions"] = FakeWorksheet(records=[tx_row(tx_id="t1")])
    data = make_data(tabs)
    df = data.load_transactions()

    assert df["datetime"].dt.tz is not None

    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
    df[df["datetime"] >= cutoff]  # no debe lanzar TypeError


def test_los_importes_no_numericos_no_rompen(make_data, tx_row):
    tabs = _empty_sheet()
    tabs["transactions"] = FakeWorksheet(records=[tx_row(amount="no-es-un-numero")])
    data = make_data(tabs)

    assert data.load_transactions()["amount"].iloc[0] == 0.0


# ── Hoja mal configurada ──────────────────────────────────────────────────────

def test_pestana_ausente_da_error_claro(make_data):
    tabs = _empty_sheet()
    del tabs["transactions"]
    data = make_data(tabs)

    with pytest.raises(data.SheetConfigError) as exc:
        data.load_transactions()

    assert "transactions" in str(exc.value)
    assert "SETUP" in str(exc.value)


def test_cabeceras_incorrectas_dan_error_claro(make_data):
    tabs = _empty_sheet()
    tabs["transactions"] = FakeWorksheet(
        records=[{"fecha": "2026-01-15", "importe": "-42.50"}]
    )
    data = make_data(tabs)

    with pytest.raises(data.SheetConfigError) as exc:
        data.load_transactions()

    mensaje = str(exc.value)
    assert "date" in mensaje and "amount" in mensaje


def test_la_pestana_opcional_puede_faltar(make_data):
    """display_category_month es opcional: su ausencia no debe romper nada."""
    tabs = _empty_sheet()
    del tabs["display_category_month"]
    data = make_data(tabs)

    df = data.load_category_month()

    assert df.empty
    assert list(df.columns) == ["category", "year_month", "amount"]
