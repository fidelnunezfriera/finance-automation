"""Prepara las pestañas y cabeceras de tu Google Sheet.

Evita tener que copiar a mano las 25 columnas de `transactions` y las 10 de
`rules`. Crea la hoja *dentro* de un fichero que ya existe: crear el fichero en
sí no se automatiza a propósito, porque una cuenta de servicio lo crearía en su
propio Drive y no podría transferirte la propiedad.

Deja además un juego de reglas de categorización en la pestaña `rules`, para
que una hoja recién creada no lo mande todo a 'Otros'. Solo las escribe si esa
pestaña está vacía. Ver DEFAULT_RULES en schema.py.

Es seguro y repetible:

  - nunca sobrescribe una celda con contenido
  - si una pestaña ya tiene la cabecera correcta, no la toca
  - si la tiene distinta, lo dice y no cambia nada
  - si `rules` ya tiene alguna regla, no añade ninguna

Uso:
    python sheets/init_sheet.py              # aplica los cambios
    python sheets/init_sheet.py --dry-run    # solo enseña qué haría
"""

import argparse
import sys
from pathlib import Path

import gspread
import yaml
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from schema import DEFAULT_RULES, TABS, default_rules_rows  # noqa: E402

HEADER_FORMAT = {
    "textFormat": {"bold": True},
    "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93},
}


def _connect() -> gspread.Spreadsheet:
    config_path = _ROOT / "config.yaml"
    if not config_path.exists():
        sys.exit("ERROR: no existe config.yaml. Ejecuta setup.bat primero.")

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    try:
        sa_rel = cfg["credentials"]["gdrive_sa"]
        sheet_id = cfg["google_sheets"]["spreadsheet_id"]
    except (KeyError, TypeError) as exc:
        sys.exit(f"ERROR: falta la clave {exc} en config.yaml.")

    sa_path = _ROOT / sa_rel
    if not sa_path.exists():
        sys.exit(f"ERROR: no se encuentra el fichero de credenciales {sa_rel}.")

    creds = Credentials.from_service_account_file(
        str(sa_path), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    try:
        return gspread.authorize(creds).open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound:
        sys.exit(
            f"ERROR: no existe ninguna hoja con el ID {sheet_id}, o no la has\n"
            "compartido como Editor con el client_email de tu cuenta de servicio."
        )


def _write_header(ws, columns: list[str]) -> None:
    """Escribe la cabecera, la congela y la resalta.

    El rango se limita a las columnas reales: formatear más allá del ancho de
    la pestaña hace que Google rechace la petición por salirse de la cuadrícula.
    """
    ultima = gspread.utils.rowcol_to_a1(1, len(columns))
    ws.update([columns], "A1")
    ws.freeze(rows=1)
    ws.format(f"A1:{ultima}", HEADER_FORMAT)


def _setup_tab(sheet, title: str, columns: list[str], dry_run: bool) -> str:
    """Deja una pestaña con su cabecera. Devuelve qué se ha hecho."""
    try:
        ws = sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        if dry_run:
            return f"CREARIA la pestana '{title}' con {len(columns)} columnas"
        ws = sheet.add_worksheet(title, rows=1000, cols=max(len(columns), 26))
        _write_header(ws, columns)
        return f"creada la pestana '{title}' con {len(columns)} columnas"

    actual = [h.strip() for h in ws.row_values(1)]

    if not actual:
        if dry_run:
            return f"ESCRIBIRIA la cabecera en '{title}' (esta vacia)"
        _write_header(ws, columns)
        return f"escrita la cabecera en '{title}'"

    faltan = [c for c in columns if c not in actual]
    if not faltan:
        return f"'{title}' ya esta correcta, no se toca"

    # Hay cabecera pero no coincide: puede haber datos debajo, no se toca nada.
    return (
        f"AVISO: '{title}' tiene una cabecera distinta y NO se ha modificado.\n"
        f"         faltan estas columnas: {', '.join(faltan)}\n"
        f"         cabecera actual: {', '.join(actual) or '(vacia)'}"
    )


def _seed_rules(sheet, dry_run: bool) -> str:
    """Siembra las reglas por defecto si la pestaña `rules` no tiene ninguna.

    Solo actúa sobre una pestaña vacía de datos. En cuanto hay una fila debajo
    de la cabecera —por defecto o propia— no se toca nada: quien haya borrado
    alguna no quiere vérsela aparecer otra vez en la siguiente ejecución.
    """
    try:
        ws = sheet.worksheet("rules")
    except gspread.WorksheetNotFound:
        # La pestaña se crea en el paso anterior; en dry-run todavía no existe.
        return (f"ANADIRIA {len(DEFAULT_RULES)} reglas en 'rules'" if dry_run
                else "AVISO: no se ha podido sembrar 'rules', la pestana no existe")

    filas = ws.get_all_values()
    if len(filas) > 1 and any(c.strip() for c in filas[1]):
        return "'rules' ya tiene reglas, no se anade ninguna"

    # Las filas se escriben por posición, pero el motor lee la pestaña por
    # nombre de columna, así que el orden de la hoja no tiene por qué ser el de
    # RULES_COLUMNS. Se toma la cabecera real para no desalinear los valores.
    cabecera = [c.strip() for c in filas[0]] if filas else []
    reglas = default_rules_rows(cabecera or None)
    if dry_run:
        return f"ANADIRIA {len(reglas)} reglas en 'rules'"

    ws.append_rows(reglas, value_input_option="USER_ENTERED")
    return f"anadidas {len(reglas)} reglas en 'rules'"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara las pestañas de tu Google Sheet.")
    parser.add_argument("--dry-run", action="store_true",
                        help="enseña lo que haría sin escribir nada")
    args = parser.parse_args()

    sheet = _connect()
    # `url` lo expone gspread; se lee con getattr para que un doble de prueba
    # sin ese atributo no tumbe el script.
    url = getattr(sheet, "url", "")

    print(f"Hoja: {sheet.title}")
    if url:
        print(f"      {url}")
    if args.dry_run:
        print("(simulacion: no se escribe nada)")
    print()

    avisos = 0
    for title, columns in TABS.items():
        resultado = _setup_tab(sheet, title, columns, args.dry_run)
        if resultado.startswith("AVISO"):
            avisos += 1
        print(f"  {resultado}")

    resultado = _seed_rules(sheet, args.dry_run)
    if resultado.startswith("AVISO"):
        avisos += 1
    print(f"  {resultado}")

    print()
    if avisos:
        print(f"Terminado con {avisos} aviso(s): revisa las pestanas de arriba.")
        print("No se ha borrado ni sobrescrito nada.")
    elif args.dry_run:
        print("Simulacion terminada. Vuelve a ejecutarlo sin --dry-run para aplicarlo.")
    else:
        print("Tu hoja esta lista. Siguiente paso: scripts/run_full_pipeline.bat")

    if url:
        print()
        print(f"Abre tu hoja aqui:  {url}")
    return 1 if avisos else 0


if __name__ == "__main__":
    raise SystemExit(main())
