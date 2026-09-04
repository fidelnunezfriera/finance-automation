"""Utilidades comunes a los tests.

Todos los tests son offline: sustituyen la conexión con Google Sheets por un
doble de prueba, así que no hacen falta credenciales ni red para ejecutarlos.
"""

import importlib.util
import sys
from pathlib import Path

import gspread
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class FakeWorksheet:
    """Sustituto de gspread.Worksheet alimentado con datos en memoria."""

    def __init__(self, records=None, values=None):
        self._records = list(records or [])
        self._values = [list(r) for r in (values or [])]

    def get_all_records(self, **kwargs):
        # gspread devuelve [] cuando la pestaña solo tiene la cabecera.
        # Acepta kwargs porque la app pasa value_render_option.
        return list(self._records)

    def get_all_values(self, **kwargs):
        return [list(r) for r in self._values]

    def get_values(self, **kwargs):
        return [list(r) for r in self._values]

    def append_rows(self, rows, value_input_option=None):
        self._values.extend([list(r) for r in rows])


class FakeSpreadsheet:
    def __init__(self, tabs):
        self._tabs = tabs

    def worksheet(self, title):
        if title not in self._tabs:
            raise gspread.WorksheetNotFound(title)
        return self._tabs[title]

    def worksheets(self):
        return list(self._tabs.values())


_constants_module = None


def data_constants():
    """Carga app/data.py aparte, solo para leer sus constantes de esquema.

    Se registra con otro nombre para no pisar el `data` que usan los tests.
    """
    global _constants_module
    if _constants_module is None:
        spec = importlib.util.spec_from_file_location(
            "_data_constants", ROOT / "app" / "data.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _constants_module = module
    return _constants_module


def _fresh_data_module():
    """Carga app/data.py aislado, sin cachés de un test anterior."""
    import streamlit as st

    st.cache_data.clear()
    st.cache_resource.clear()

    spec = importlib.util.spec_from_file_location("data", ROOT / "app" / "data.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["data"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def make_data():
    """Devuelve una fábrica: pásale las pestañas y te da app/data.py listo.

        data = make_data({"transactions": FakeWorksheet(records=[...])})
    """

    def _factory(tabs):
        module = _fresh_data_module()
        module._spreadsheet = lambda: FakeSpreadsheet(tabs)
        return module

    yield _factory

    import streamlit as st

    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture
def tx_row():
    """Una transacción válida: todas las columnas documentadas rellenas."""

    def _factory(**overrides):
        row = {c: "" for c in data_constants()._TX_COLUMNS}
        row.update(
            {
                "tx_id": "t1",
                "date": "2026-01-15",
                "datetime": "2026-01-15 10:00:00",
                "amount": "-42.50",
                "currency": "EUR",
                "merchant_raw": "MERCADONA",
                "merchant_norm": "mercadona",
                "category": "Alimentacion",
                "rule_confidence": "1",
                "type": "expense",
                "event_domain": "spending",
                "tipus": "GASTO",
                "year_month": "2026-01",
            }
        )
        row.update(overrides)
        return row

    return _factory
