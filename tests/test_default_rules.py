"""Las reglas de ejemplo que `init_sheet.py` siembra en una hoja nueva.

Son lo primero que ve alguien que acaba de instalar el proyecto, y la
plantilla que va a copiar para escribir las suyas, así que importa que hagan
exactamente lo que dicen y que entre todas enseñen el repertorio completo.

Se evalúan con el motor real, no con una reimplementación.
"""

import pytest

from schema import RULES_COLUMNS, DEFAULT_RULES, default_rules_rows
from pipeline.apply_rules_to_sheet import run_rules


@pytest.fixture(scope="module")
def reglas():
    """Las reglas de ejemplo en la forma que espera run_rules, ya ordenadas."""
    salida = []
    for fila in default_rules_rows():
        d = dict(zip(RULES_COLUMNS, fila))
        d["priority"] = int(d["priority"])
        salida.append(d)
    salida.sort(key=lambda r: r["priority"])
    return salida


def _categoriza(reglas, **tx):
    cat, sub, rid, _conf = run_rules(reglas, tx)
    return cat, sub, rid


# ── regex: agrupan varios comercios en una regla ──────────────────────────────

@pytest.mark.parametrize("merchant,categoria,subcategoria", [
    ("mercadona 1234",       "Compra",      "Supermercado"),
    ("carrefour express",    "Compra",      "Supermercado"),
    ("supermercados dia sa", "Compra",      "Supermercado"),
    ("glovoapp",             "Dispensable", "Restauración"),
    ("just eat spain",       "Dispensable", "Restauración"),
    ("repsol e s madrid",    "Transporte",  "Combustible"),
    ("bp oil espana sl",     "Transporte",  "Combustible"),
    ("cabify espana",        "Transporte",  "Transporte público"),
    ("renfe viajeros",       "Transporte",  "Transporte público"),
    ("netflix.com",          "Dispensable", "Suscripciones"),
    ("hbo max",              "Dispensable", "Suscripciones"),
])
def test_regex_agrupa_comercios(reglas, merchant, categoria, subcategoria):
    cat, sub, _ = _categoriza(reglas, merchant_norm=merchant)
    assert (cat, sub) == (categoria, subcategoria)


@pytest.mark.parametrize("merchant", [
    "media markt",           # contiene 'dia'
    "metrovacesa",           # contiene 'metro'
    "boltoneria industrial",  # contiene 'bolt'
])
def test_las_fronteras_de_palabra_evitan_falsos_positivos(reglas, merchant):
    """Sin los \\b, estos caerían en supermercado o transporte."""
    _cat, _sub, rid = _categoriza(reglas, merchant_norm=merchant)
    assert rid == "ejemplo-otros"


@pytest.mark.parametrize("merchant", ["uber eats madrid", "ubereats"])
def test_uber_eats_es_restauracion_no_transporte(reglas, merchant):
    """Lo único que los separa es el orden de prioridad.

    Si alguien reordena las reglas y restauración cae después de transporte,
    los pedidos de comida pasan a contar como transporte sin que nada falle.
    """
    cat, sub, _ = _categoriza(reglas, merchant_norm=merchant)
    assert (cat, sub) == ("Dispensable", "Restauración")


def test_uber_a_secas_sigue_siendo_transporte(reglas):
    cat, sub, _ = _categoriza(reglas, merchant_norm="uber bv")
    assert (cat, sub) == ("Transporte", "Transporte público")


# ── contains y equals ─────────────────────────────────────────────────────────

def test_contains_casa_como_subcadena(reglas):
    cat, sub, _ = _categoriza(reglas, merchant_norm="farmacia san pablo")
    assert (cat, sub) == ("Salud", "Farmacia")


def test_equals_casa_el_valor_exacto(reglas):
    cat, _sub, rid = _categoriza(reglas, merchant_norm="bizum")
    assert cat == "Transferencia/Bizum"
    assert rid == "ejemplo-bizum"


@pytest.mark.parametrize("merchant", ["bizum enviado", "envio bizum", "bizumm"])
def test_equals_no_casa_por_subcadena(reglas, merchant):
    """Es la diferencia con contains, y la razón de usar equals aquí."""
    _cat, _sub, rid = _categoriza(reglas, merchant_norm=merchant)
    assert rid == "ejemplo-otros"


# ── Las estructurales: el tipo manda sobre el comercio ────────────────────────

@pytest.mark.parametrize("tipo,subcategoria,rule_id", [
    ("buy",      "Compra Activos", "tr-compra"),
    ("sell",     "Venta Activos",  "tr-venta"),
    ("dividend", "Dividendo",      "tr-dividendo"),
    ("interest", "Intereses",      "tr-intereses"),
])
def test_los_movimientos_de_inversion_van_por_tipo(reglas, tipo, subcategoria, rule_id):
    cat, sub, rid = _categoriza(reglas, merchant_norm="lo que sea", type=tipo)
    assert (cat, sub, rid) == ("Inversión", subcategoria, rule_id)


def test_el_tipo_gana_a_una_regla_de_texto(reglas):
    """Comprar acciones de Netflix no es una suscripción a Netflix.

    Es la razón de que las estructurales lleven prioridad de dos cifras: si
    cayeran por debajo de las reglas de comercio, cualquier activo cuyo nombre
    coincida con un comercio conocido se categorizaría como gasto.
    """
    cat, sub, _ = _categoriza(reglas, merchant_norm="netflix inc", type="buy")
    assert (cat, sub) == ("Inversión", "Compra Activos")


def test_las_estructurales_van_antes_que_las_de_ejemplo():
    estructurales = [r for r in DEFAULT_RULES if r["rule_id"].startswith("tr-")]
    ejemplos = [r for r in DEFAULT_RULES if r["rule_id"].startswith("ejemplo-")]
    assert estructurales, "deberían existir reglas estructurales"
    assert max(r["priority"] for r in estructurales) < min(r["priority"] for r in ejemplos)


def test_las_estructurales_no_dependen_del_comercio():
    """Valen para cualquier usuario porque no miran texto de comercios."""
    for regla in DEFAULT_RULES:
        if regla["rule_id"].startswith("tr-"):
            assert regla["match_field"] == "type"


# ── match_field distinto de merchant_norm ─────────────────────────────────────

def test_una_regla_puede_mirar_el_tipo_de_movimiento(reglas):
    cat, sub, rid = _categoriza(reglas, merchant_norm="iberdrola sa", type="dividend")
    assert (cat, sub, rid) == ("Inversión", "Dividendo", "tr-dividendo")


def test_el_mismo_comercio_sin_ese_tipo_no_casa_esa_regla(reglas):
    _cat, _sub, rid = _categoriza(reglas, merchant_norm="iberdrola sa", type="card")
    assert rid != "tr-dividendo"


# ── direction ─────────────────────────────────────────────────────────────────

def test_direction_acota_por_signo(reglas):
    cat, sub, _ = _categoriza(reglas, merchant_norm="nomina empresa sa", tipus="in")
    assert (cat, sub) == ("Ingresos", "Nómina")


def test_direction_descarta_el_sentido_contrario(reglas):
    """Una devolución con el mismo texto no debe contar como nómina."""
    _cat, _sub, rid = _categoriza(reglas, merchant_norm="nomina empresa sa", tipus="out")
    assert rid == "ejemplo-otros"


# ── exists: el cajón de sastre ────────────────────────────────────────────────

@pytest.mark.parametrize("merchant", [
    "hospital la paz",
    "un comercio que no ha visto nadie",
])
def test_lo_no_reclamado_cae_en_otros(reglas, merchant):
    cat, _sub, rid = _categoriza(reglas, merchant_norm=merchant)
    assert (cat, rid) == ("Otros", "ejemplo-otros")


def test_sin_comercio_no_casa_ninguna_regla(reglas):
    """`exists` es lo único que separa 'sin categorizar' de 'Otros'.

    Una fila sin merchant_norm no la recoge ni el cajón de sastre: se queda
    con rule_confidence 0, que es lo que permite distinguirla.
    """
    assert _categoriza(reglas, merchant_norm="") == ("", "", "")


def test_el_cajon_de_sastre_va_el_ultimo():
    """Si otra regla tuviera una prioridad mayor, nunca se aplicaría."""
    otros = next(r for r in DEFAULT_RULES if r["rule_id"] == "ejemplo-otros")
    resto = [r for r in DEFAULT_RULES if r["rule_id"] != "ejemplo-otros"]
    assert all(r["priority"] < otros["priority"] for r in resto)


def test_una_regla_propia_puede_colarse_antes_del_cajon(reglas):
    """99999 deja sitio de sobra por debajo para reglas del usuario."""
    propia = {
        "rule_id": "mi-regla", "priority": 500, "match_field": "merchant_norm",
        "match_type": "contains", "match_value": "peluqueria",
        "category": "Peluquería", "subcategory": "", "applies_to_type": "",
        "direction": "",
    }
    ampliadas = sorted(reglas + [propia], key=lambda r: r["priority"])
    cat, _sub, rid = _categoriza(ampliadas, merchant_norm="peluqueria lola")
    assert (cat, rid) == ("Peluquería", "mi-regla")


# ── Cobertura didáctica ───────────────────────────────────────────────────────

def test_los_ejemplos_cubren_los_cuatro_tipos_de_comparacion():
    """Son la documentación viva del formato: si falta uno, no se enseña."""
    assert {r["match_type"] for r in DEFAULT_RULES} == {
        "contains", "equals", "regex", "exists"}


def test_las_regex_compilan():
    import re
    for regla in DEFAULT_RULES:
        if regla["match_type"] == "regex":
            re.compile(regla["match_value"])


# ── Forma de las filas ────────────────────────────────────────────────────────

def test_las_filas_siguen_el_orden_de_las_columnas():
    filas = default_rules_rows()
    assert len(filas) == len(DEFAULT_RULES)
    assert all(len(f) == len(RULES_COLUMNS) for f in filas)


def test_todas_vienen_activadas():
    i = RULES_COLUMNS.index("enabled")
    assert all(f[i] == "TRUE" for f in default_rules_rows())


def test_las_prioridades_no_empatan():
    prioridades = [r["priority"] for r in DEFAULT_RULES]
    assert len(set(prioridades)) == len(prioridades)
