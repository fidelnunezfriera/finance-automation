"""Esquema de las pestañas de Google Sheets: fuente única de verdad.

Lo consumen el dashboard (`app/data.py`), la derivación de posiciones
(`pipeline/derive_positions.py`) y el script que prepara la hoja
(`sheets/init_sheet.py`).

Antes estas listas vivían duplicadas en cada uno de esos ficheros y en
SETUP.md, así que cambiar una columna obligaba a acordarse de los cuatro.
Si añades o renombras una columna, hazlo aquí.
"""

TRANSACTIONS_COLUMNS = [
    "tx_id", "source", "source_file", "import_batch_id", "date", "datetime",
    "amount", "currency", "merchant_raw", "merchant_norm", "description",
    "category", "subcategory", "rule_id", "rule_confidence", "type", "account",
    "status", "notes", "created_at", "raw_json", "rule_notes", "event_domain",
    "tipus", "year_month",
]

RULES_COLUMNS = [
    "rule_id", "enabled", "priority", "direction", "applies_to_type",
    "match_field", "match_type", "match_value", "category", "subcategory",
]

POSITIONS_COLUMNS = [
    "snapshot_at", "isin", "name", "quantity", "status", "adjusted", "anomaly",
]

# La pestaña del gráfico de categorías es una tabla dinámica que se construye a
# mano; sólo se crea la cabecera de la primera columna para que exista el sitio.
CATEGORY_MONTH_COLUMNS = ["category"]


# Pestañas que `sheets/init_sheet.py` deja preparadas. `positions` la reescribe
# el pipeline en cada ejecución, pero se crea igualmente para que el dashboard
# pueda abrirse antes de la primera pasada.
TABS = {
    "transactions": TRANSACTIONS_COLUMNS,
    "rules": RULES_COLUMNS,
    "positions": POSITIONS_COLUMNS,
    "display_category_month": CATEGORY_MONTH_COLUMNS,
}

# Pestañas sin las que el dashboard no puede funcionar.
REQUIRED_TABS = ("transactions", "positions")


# ---------------------------------------------------------------------------
# Reglas por defecto
#
# `init_sheet.py` las escribe en la pestaña `rules` solo cuando está vacía, para
# que una instalación nueva categorice algo desde la primera ejecución en vez de
# dejarlo todo sin categoría.
#
# Son de dos clases distintas y conviene no confundirlas:
#
#   tr-*       ESTRUCTURALES. Categorizan los movimientos de Trade Republic por
#              el tipo que dedujo el pipeline, no por el texto del comercio. No
#              dependen de los hábitos de nadie: una venta es una venta. Valen
#              igual para cualquier usuario y lo normal es dejarlas.
#
#   ejemplo-*  DE EJEMPLO. Categorizan por comercio, así que solo aciertan si
#              compras donde dice. Están sobre todo para enseñar el formato: se
#              esperan borradas, duplicadas o adaptadas.
#
# Las estructurales van con prioridad de dos cifras para que ganen siempre a
# cualquier regla de texto: un dividendo de Iberdrola es un dividendo, no una
# compra en Iberdrola. Queda todo el rango de 50 en adelante libre.
#
# Gana la primera regla que casa, por orden de `priority` ascendente. Las
# comparaciones no distinguen mayúsculas.
#
# Entre todas cubren los cuatro tipos de comparación y los dos campos que
# acotan a qué movimientos se aplica una regla:
#
#   contains  la subcadena aparece en el campo — el caso normal
#   equals    el campo es exactamente ese valor
#   regex     expresión regular — para agrupar muchos comercios en una regla
#   exists    el campo tiene algo, sea lo que sea
#
#   match_field       merchant_norm (lo habitual) o type
#   direction         'in' o 'out', para acotar por signo del importe
#   applies_to_type   limita a un tipo de movimiento concreto
#
# En las regex, los \b son deliberados: sin ellos 'dia' casa dentro de 'media
# markt', 'metro' dentro de 'metrovacesa' y 'bp' dentro de cualquier palabra
# que lo contenga.
#
# La última es un cajón de sastre: con `exists` y la prioridad más alta, recoge
# todo lo que ninguna regla anterior haya reclamado. Sin ella esos movimientos
# se quedarían sin categoría y sin rule_id, que es más difícil de revisar en la
# hoja que verlos agrupados en 'Otros'.
#
# La guía para escribir reglas propias está en docs/REGLAS.md.
# ---------------------------------------------------------------------------

DEFAULT_RULES = [
    # ── Estructurales: el tipo de movimiento manda sobre el comercio ─────────
    #
    # `applies_to_type` repite la condición de `match_field`/`match_value`. Es
    # redundante y deliberado: son las reglas tal cual llevan funcionando en
    # producción, y el doble filtro no cuesta nada.
    {
        "rule_id": "tr-compra",
        "match_field": "type",
        "match_type": "equals",
        "match_value": "buy",
        "applies_to_type": "buy",
        "category": "Inversión",
        "subcategory": "Compra Activos",
        "priority": 10,
    },
    {
        "rule_id": "tr-venta",
        "match_field": "type",
        "match_type": "equals",
        "match_value": "sell",
        "applies_to_type": "sell",
        "category": "Inversión",
        "subcategory": "Venta Activos",
        "priority": 20,
    },
    {
        "rule_id": "tr-dividendo",
        "match_field": "type",
        "match_type": "equals",
        "match_value": "dividend",
        "applies_to_type": "dividend",
        "category": "Inversión",
        "subcategory": "Dividendo",
        "priority": 30,
    },
    {
        "rule_id": "tr-intereses",
        "match_field": "type",
        "match_type": "equals",
        "match_value": "interest",
        "applies_to_type": "interest",
        "category": "Inversión",
        "subcategory": "Intereses",
        "priority": 40,
    },

    # ── De ejemplo: categorizan por comercio ────────────────────────────────
    {
        # `direction` acota a los movimientos que entran, así que una
        # devolución con el mismo texto no se cuela como nómina.
        "rule_id": "ejemplo-nomina",
        "match_type": "contains",
        "match_value": "nomina",
        "direction": "in",
        "category": "Ingresos",
        "subcategory": "Nómina",
        "priority": 85,
    },
    {
        # `equals` y no `contains`: 'bizum' es un comercio normalizado exacto y
        # 'bizum enviado' es otro distinto.
        "rule_id": "ejemplo-bizum",
        "match_type": "equals",
        "match_value": "bizum",
        "category": "Transferencia/Bizum",
        "subcategory": "",
        "priority": 90,
    },
    {
        "rule_id": "ejemplo-supermercado",
        "match_type": "regex",
        "match_value": r"mercadona|carrefour|lidl|alcampo|eroski|consum|ahorramas|\bdia\b",
        "category": "Compra",
        "subcategory": "Supermercado",
        "priority": 100,
    },
    {
        # Va antes que transporte a propósito: si no, 'uber eats' caería en
        # transporte por el 'uber'.
        "rule_id": "ejemplo-restauracion",
        "match_type": "regex",
        "match_value": r"glovo|just ?eat|uber ?eats|telepizza|domino|mcdonald|burger king|starbucks",
        "category": "Dispensable",
        "subcategory": "Restauración",
        "priority": 110,
    },
    {
        "rule_id": "ejemplo-combustible",
        "match_type": "regex",
        "match_value": r"repsol|cepsa|galp|petroprix|\bshell\b|\bbp\b",
        "category": "Transporte",
        "subcategory": "Combustible",
        "priority": 120,
    },
    {
        "rule_id": "ejemplo-transporte",
        "match_type": "regex",
        "match_value": r"\buber\b|cabify|\bbolt\b|renfe|blablacar|\bemt\b|\bmetro\b",
        "category": "Transporte",
        "subcategory": "Transporte público",
        "priority": 130,
    },
    {
        "rule_id": "ejemplo-suscripciones",
        "match_type": "regex",
        "match_value": r"netflix|spotify|\bhbo\b|disney|filmin|movistar|prime video",
        "category": "Dispensable",
        "subcategory": "Suscripciones",
        "priority": 140,
    },
    {
        # El caso más simple posible, para copiar y pegar.
        "rule_id": "ejemplo-farmacia",
        "match_type": "contains",
        "match_value": "farmacia",
        "category": "Salud",
        "subcategory": "Farmacia",
        "priority": 150,
    },
    {
        # Cajón de sastre. `exists` ignora match_value: casa con cualquier fila
        # que tenga comercio. La prioridad altísima la deja siempre la última,
        # y deja sitio de sobra para intercalar reglas propias por debajo.
        "rule_id": "ejemplo-otros",
        "match_type": "exists",
        "match_value": "",
        "category": "Otros",
        "subcategory": "",
        "priority": 99999,
    },
]


def default_rules_rows(columns: list[str] | None = None) -> list[list]:
    """Las reglas por defecto como filas, alineadas con `columns`.

    El motor de reglas lee la pestaña por nombre de columna, así que el orden
    de la hoja es libre y puede no coincidir con RULES_COLUMNS. Pero escribir
    filas es posicional: hay que pasar la cabecera REAL de la pestaña, o los
    valores acaban en la columna de al lado.

    Sin argumento usa RULES_COLUMNS, que es el orden que crea `init_sheet.py`
    en una pestaña nueva.
    """
    columns = columns or RULES_COLUMNS
    # Valores por defecto: cada regla solo declara lo que se sale de la norma.
    comunes = {"enabled": "TRUE", "match_field": "merchant_norm",
               "applies_to_type": "", "direction": "", "subcategory": ""}
    return [[{**comunes, **regla}.get(col, "") for col in columns]
            for regla in DEFAULT_RULES]
