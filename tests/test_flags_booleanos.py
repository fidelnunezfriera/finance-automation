"""Las columnas de bandera (`anomaly`, `adjusted`) y cómo llegan de la hoja.

`derive_positions.py` escribe los textos "TRUE"/"FALSE", pero Google los
interpreta y los guarda como casillas booleanas. Al pedir los valores sin
formatear vuelven como `True`/`False` de Python.

Eso rompió el dashboard de dos formas a la vez: una columna donde todas las
filas son booleanas deja de admitir `.str`, y una comparación contra la cadena
"TRUE" nunca vuelve a ser cierta. La segunda no da error: simplemente el aviso
de anomalía deja de salir.

Aquí se fija que las dos formas de guardarlo lleguen igual al dashboard.
"""

import pandas as pd
import pytest

from conftest import FakeSpreadsheet, FakeWorksheet, data_constants

data = data_constants()
POS_COLUMNS = data._POS_COLUMNS


def _posiciones(monkeypatch, filas):
    completas = []
    for f in filas:
        fila = {c: "" for c in POS_COLUMNS}
        fila.update(f)
        completas.append(fila)
    monkeypatch.setattr(data, "_spreadsheet", lambda: FakeSpreadsheet(
        {"positions": FakeWorksheet(records=completas)}))
    data.load_positions.clear()
    return data.load_positions()


# ── Las dos formas de guardarlo ───────────────────────────────────────────────

def test_booleanos_de_verdad(monkeypatch):
    """Lo que devuelve Google cuando la celda es una casilla."""
    df = _posiciones(monkeypatch, [
        {"isin": "A", "quantity": 1, "status": "open", "anomaly": True,  "adjusted": False},
        {"isin": "B", "quantity": 2, "status": "open", "anomaly": False, "adjusted": True},
    ])
    assert df["anomaly"].tolist() == [True, False]
    assert df["adjusted"].tolist() == [False, True]


def test_texto_en_mayusculas(monkeypatch):
    """Lo que había antes de pedir los valores sin formatear."""
    df = _posiciones(monkeypatch, [
        {"isin": "A", "quantity": 1, "status": "open", "anomaly": "TRUE"},
        {"isin": "B", "quantity": 2, "status": "open", "anomaly": "FALSE"},
    ])
    assert df["anomaly"].tolist() == [True, False]


def test_celdas_vacias_cuentan_como_falso(monkeypatch):
    df = _posiciones(monkeypatch, [
        {"isin": "A", "quantity": 1, "status": "open", "anomaly": ""},
    ])
    assert df["anomaly"].tolist() == [False]


def test_mezcla_de_texto_y_booleano(monkeypatch):
    """El caso que hacía que fallara en una maquina y no en otra."""
    df = _posiciones(monkeypatch, [
        {"isin": "A", "quantity": 1, "status": "open", "anomaly": True},
        {"isin": "B", "quantity": 2, "status": "open", "anomaly": ""},
        {"isin": "C", "quantity": 3, "status": "open", "anomaly": "TRUE"},
    ])
    assert df["anomaly"].tolist() == [True, False, True]


# ── Lo que el dashboard hace con ellas ────────────────────────────────────────

def test_la_columna_admite_any(monkeypatch):
    """`open_pos["anomaly"].any()` es lo que consulta la pagina de Activos."""
    df = _posiciones(monkeypatch, [
        {"isin": "A", "quantity": 1, "status": "open", "anomaly": True},
        {"isin": "B", "quantity": 2, "status": "open", "anomaly": False},
    ])
    assert df["anomaly"].any()

    sin = _posiciones(monkeypatch, [
        {"isin": "A", "quantity": 1, "status": "open", "anomaly": False},
    ])
    assert not sin["anomaly"].any()


# ── El conversor, caso a caso ─────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    (True, True), (False, False),
    ("TRUE", True), ("true", True), ("True", True),
    ("FALSE", False), ("false", False),
    ("VERDADERO", True), ("si", True), ("sí", True), ("yes", True), ("x", True),
    ("1", True), (1, True), (0, False),
    ("", False), ("   ", False), ("no", False), ("cualquier cosa", False),
])
def test_a_bool(entrada, esperado):
    assert data._a_bool(entrada) is esperado


# ── El accesor .str en las columnas de texto ──────────────────────────────────

def test_event_domain_numerico_no_tumba_la_carga(monkeypatch):
    """Defensa contra el mismo fallo en las transacciones.

    Si una hoja acabara con esas columnas en numerico o booleano, `.str`
    fallaria igual. Se convierten a texto antes de usar el accesor.
    """
    fila = {c: "" for c in data._TX_COLUMNS}
    fila.update({"tx_id": "a", "date": "2026-01-01", "datetime": "2026-01-01T00:00:00",
                 "amount": -1.0, "event_domain": 0, "tipus": 1})
    monkeypatch.setattr(data, "_spreadsheet", lambda: FakeSpreadsheet(
        {"transactions": FakeWorksheet(records=[fila])}))
    data.load_transactions.clear()
    df = data.load_transactions()

    assert df["event_domain_l"].iloc[0] == "0"
    assert df["tipus_norm"].iloc[0] == "1"
