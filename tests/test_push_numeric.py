"""Los importes tienen que llegar a Google Sheets como números.

Si viajan como texto, Google los interpreta según la configuración regional de
la hoja: en es_ES el punto es separador de miles, así que "177.5148"
participaciones se convertían en 1.775.148. En es_MX o en_US el mismo texto
entra bien, y por eso el fallo sólo se veía en algunas hojas.
"""

import importlib.util
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT))


def _load_push():
    spec = importlib.util.spec_from_file_location(
        "push_to_sheets", ROOT / "sheets" / "push_to_sheets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


push = _load_push()

HEADERS = ["tx_id", "date", "amount", "merchant_raw", "rule_confidence", "year_month"]


def _fila(**campos):
    base = {h: "" for h in HEADERS}
    base.update({"tx_id": "t1", "date": "2026-01-15", "amount": "-401.07",
                 "merchant_raw": "MERCADONA", "rule_confidence": "1",
                 "year_month": "2026-01"})
    base.update(campos)
    return push._row_values(base, HEADERS)


@pytest.mark.parametrize("texto,esperado", [
    ("-401.07", -401.07),
    ("1.234", 1.234),        # el caso que se convertia en 1234
    ("177.5148", 177.5148),  # el que se convertia en 1.775.148
    ("0.000640", 0.00064),
    ("1739.23", 1739.23),
])
def test_los_importes_viajan_como_numero(texto, esperado):
    valores = _fila(amount=texto)
    importe = valores[HEADERS.index("amount")]

    assert isinstance(importe, float), f"{texto!r} deberia viajar como float"
    assert importe == pytest.approx(esperado)


def test_la_confianza_de_regla_tambien_es_numero():
    valores = _fila(rule_confidence="0.85")
    assert valores[HEADERS.index("rule_confidence")] == pytest.approx(0.85)


def test_el_texto_sigue_siendo_texto():
    """Sólo se convierten las columnas numéricas; el resto se respeta."""
    valores = _fila()

    assert valores[HEADERS.index("merchant_raw")] == "MERCADONA"
    assert valores[HEADERS.index("year_month")] == "2026-01"
    assert valores[HEADERS.index("date")] == "2026-01-15"
    assert valores[HEADERS.index("tx_id")] == "t1"


def test_year_month_no_se_convierte_en_numero():
    """'2026-01' es una etiqueta: como número seria una resta."""
    valores = _fila()
    assert isinstance(valores[HEADERS.index("year_month")], str)


def test_un_importe_vacio_no_se_vuelve_cero():
    valores = _fila(amount="")
    assert valores[HEADERS.index("amount")] == ""


def test_un_importe_no_numerico_se_conserva():
    """Mejor una celda de texto visible que un cero inventado."""
    valores = _fila(amount="pendiente")
    assert valores[HEADERS.index("amount")] == "pendiente"
