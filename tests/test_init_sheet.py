"""Preparación automática de las pestañas de la hoja.

Lo crítico aquí es que el script no destruya nada: se ejecuta contra la hoja
real de alguien, posiblemente varias veces y con datos dentro.
"""

import importlib.util
import sys

import gspread
import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT))
from schema import RULES_COLUMNS, TABS, TRANSACTIONS_COLUMNS  # noqa: E402


def _load_init_sheet():
    spec = importlib.util.spec_from_file_location(
        "init_sheet", ROOT / "sheets" / "init_sheet.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init_sheet = _load_init_sheet()


class SpyWorksheet:
    """Pestaña que registra toda escritura, para comprobar que no las hay."""

    def __init__(self, header=None, rows=None):
        self._header = list(header or [])
        self._rows = list(rows or [])
        self.escrituras = []

    def row_values(self, row):
        return list(self._header) if row == 1 else []

    def update(self, values, range_name=None, **kwargs):
        self.escrituras.append(("update", values, range_name))
        self._header = list(values[0])

    def freeze(self, rows=None, cols=None):
        self.escrituras.append(("freeze", rows))

    def format(self, ranges, format):
        self.escrituras.append(("format", ranges))


class SpySpreadsheet:
    title = "Hoja de prueba"

    def __init__(self, tabs=None):
        self.tabs = dict(tabs or {})
        self.creadas = []

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols, index=None):
        self.creadas.append(title)
        ws = SpyWorksheet()
        self.tabs[title] = ws
        return ws


# ── Hoja nueva ────────────────────────────────────────────────────────────────

def test_crea_las_pestanas_que_faltan():
    sheet = SpySpreadsheet()

    for title, columns in TABS.items():
        init_sheet._setup_tab(sheet, title, columns, dry_run=False)

    assert sheet.creadas == list(TABS)
    escrito = sheet.tabs["transactions"].escrituras[0]
    assert escrito[1] == [TRANSACTIONS_COLUMNS]
    assert escrito[2] == "A1"


def test_la_cabecera_escrita_es_la_del_esquema():
    sheet = SpySpreadsheet()
    init_sheet._setup_tab(sheet, "rules", RULES_COLUMNS, dry_run=False)

    assert sheet.tabs["rules"].row_values(1) == RULES_COLUMNS


def test_congela_y_resalta_la_cabecera():
    sheet = SpySpreadsheet()
    init_sheet._setup_tab(sheet, "rules", RULES_COLUMNS, dry_run=False)

    acciones = [a[0] for a in sheet.tabs["rules"].escrituras]
    assert "freeze" in acciones and "format" in acciones


def test_el_formato_no_se_sale_de_la_cuadricula():
    """Formatear más allá del ancho de la pestaña lo rechaza la API de Google."""
    sheet = SpySpreadsheet()
    init_sheet._setup_tab(sheet, "transactions", TRANSACTIONS_COLUMNS, dry_run=False)

    rangos = [a[1] for a in sheet.tabs["transactions"].escrituras if a[0] == "format"]
    assert rangos == ["A1:Y1"], f"esperado A1:Y1 (25 columnas), obtenido {rangos}"


# ── Repetir la ejecución ──────────────────────────────────────────────────────

def test_no_toca_una_pestana_ya_correcta():
    ws = SpyWorksheet(header=TRANSACTIONS_COLUMNS)
    sheet = SpySpreadsheet({"transactions": ws})

    resultado = init_sheet._setup_tab(sheet, "transactions", TRANSACTIONS_COLUMNS, dry_run=False)

    assert ws.escrituras == [], "no debe escribir en una pestaña ya correcta"
    assert "ya esta correcta" in resultado


def test_escribe_la_cabecera_si_la_pestana_esta_vacia():
    ws = SpyWorksheet(header=[])
    sheet = SpySpreadsheet({"transactions": ws})

    init_sheet._setup_tab(sheet, "transactions", TRANSACTIONS_COLUMNS, dry_run=False)

    assert ws.row_values(1) == TRANSACTIONS_COLUMNS


# ── Seguridad: nunca destruir datos ───────────────────────────────────────────

def test_nunca_sobrescribe_una_cabecera_distinta():
    """El caso peligroso: alguien con datos y otra cabecera."""
    ws = SpyWorksheet(header=["fecha", "importe", "concepto"],
                      rows=[["2026-01-01", "-10", "compra"]])
    sheet = SpySpreadsheet({"transactions": ws})

    resultado = init_sheet._setup_tab(sheet, "transactions", TRANSACTIONS_COLUMNS, dry_run=False)

    assert ws.escrituras == [], "NO debe escribir sobre datos existentes"
    assert ws.row_values(1) == ["fecha", "importe", "concepto"]
    assert resultado.startswith("AVISO")
    assert "date" in resultado


def test_dry_run_no_escribe_nada():
    sheet = SpySpreadsheet({"transactions": SpyWorksheet(header=[])})

    for title, columns in TABS.items():
        init_sheet._setup_tab(sheet, title, columns, dry_run=True)

    assert sheet.creadas == []
    assert sheet.tabs["transactions"].escrituras == []


# ── Coherencia del esquema ────────────────────────────────────────────────────

def test_el_esquema_es_el_mismo_en_todo_el_proyecto():
    """schema.py es la fuente única: nadie debe llevar su propia copia."""
    from conftest import data_constants

    import importlib
    dp_spec = importlib.util.spec_from_file_location(
        "derive_positions", ROOT / "pipeline" / "derive_positions.py"
    )
    dp = importlib.util.module_from_spec(dp_spec)
    dp_spec.loader.exec_module(dp)

    from schema import POSITIONS_COLUMNS

    assert data_constants()._TX_COLUMNS == TRANSACTIONS_COLUMNS
    assert data_constants()._POS_COLUMNS == POSITIONS_COLUMNS
    assert dp.COLUMNS == POSITIONS_COLUMNS


@pytest.mark.parametrize("tab", ["transactions", "rules", "positions"])
def test_no_hay_columnas_repetidas(tab):
    columnas = TABS[tab]
    assert len(columnas) == len(set(columnas))


# ── Reglas de ejemplo ─────────────────────────────────────────────────────────

def _hoja_con_rules(values):
    from conftest import FakeSpreadsheet, FakeWorksheet
    return FakeSpreadsheet({"rules": FakeWorksheet(values=values)})


def test_siembra_las_reglas_en_una_pestana_vacia():
    from schema import DEFAULT_RULES

    hoja = _hoja_con_rules([RULES_COLUMNS])
    resultado = init_sheet._seed_rules(hoja, dry_run=False)

    filas = hoja.worksheet("rules").get_all_values()
    assert len(filas) == 1 + len(DEFAULT_RULES)
    assert "anadidas" in resultado


def test_no_vuelve_a_sembrar_si_ya_hay_reglas():
    """Quien borre las de ejemplo no debe vérselas reaparecer al reejecutar."""
    propia = ["mi-regla", "TRUE", "merchant_norm", "contains", "peluqueria",
              "Peluquería", "", "", "", "10"]
    hoja = _hoja_con_rules([RULES_COLUMNS, propia])

    init_sheet._seed_rules(hoja, dry_run=False)

    filas = hoja.worksheet("rules").get_all_values()
    assert len(filas) == 2
    assert filas[1] == propia


def test_una_pestana_con_solo_cabecera_cuenta_como_vacia():
    """Una fila de celdas en blanco bajo la cabecera no es una regla."""
    from schema import DEFAULT_RULES

    hoja = _hoja_con_rules([RULES_COLUMNS, ["" for _ in RULES_COLUMNS]])
    init_sheet._seed_rules(hoja, dry_run=False)

    assert len(hoja.worksheet("rules").get_all_values()) == 2 + len(DEFAULT_RULES)


def test_se_alinea_con_la_cabecera_real_de_la_hoja():
    """El motor lee por nombre de columna, así que el orden de la hoja es libre.

    Escribir las filas es posicional: si se usara el orden de RULES_COLUMNS en
    una pestaña ordenada de otra forma, cada valor caería en la columna de al
    lado sin que nada fallara — quedarían reglas con la categoría en 'priority'.
    """
    cabecera = ["rule_id", "category", "subcategory", "enabled", "priority",
                "match_field", "match_type", "match_value", "direction",
                "applies_to_type"]
    hoja = _hoja_con_rules([cabecera])

    init_sheet._seed_rules(hoja, dry_run=False)

    filas = hoja.worksheet("rules").get_all_values()
    escritas = {f[cabecera.index("rule_id")]: dict(zip(cabecera, f))
                for f in filas[1:]}

    # Se comprueba contra la definición, no contra valores copiados aquí: así
    # el test sigue valiendo aunque cambien las reglas de ejemplo.
    from schema import DEFAULT_RULES

    assert set(escritas) == {r["rule_id"] for r in DEFAULT_RULES}
    for regla in DEFAULT_RULES:
        escrita = escritas[regla["rule_id"]]
        for campo, valor in regla.items():
            assert escrita[campo] == valor, f"{regla['rule_id']}.{campo}"
        assert escrita["enabled"] == "TRUE"


def test_dry_run_no_escribe_nada():
    hoja = _hoja_con_rules([RULES_COLUMNS])
    resultado = init_sheet._seed_rules(hoja, dry_run=True)

    assert len(hoja.worksheet("rules").get_all_values()) == 1
    assert "ANADIRIA" in resultado
