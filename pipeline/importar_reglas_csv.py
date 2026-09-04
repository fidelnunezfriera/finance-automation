#!/usr/bin/env python3
"""
Añade reglas nuevas desde un CSV a la pestaña `rules`, y reordena toda la
pestaña por `priority` ascendente al terminar.

Pensado para el CSV que devuelve un LLM siguiendo docs/GENERAR_REGLAS.md,
pero vale para cualquier CSV con las columnas de RULES_COLUMNS
(schema.py): rule_id, enabled, priority, direction, applies_to_type,
match_field, match_type, match_value, category, subcategory.

No borra ni sobrescribe ninguna regla existente: solo añade filas nuevas
y reordena las que ya había junto a ellas. Si un rule_id del CSV ya existe
en la hoja (o se repite dentro del propio CSV), esa fila se salta -- para
no duplicar ni pisar una regla tuya sin avisar.

Uso:
    python pipeline/importar_reglas_csv.py out/propuesta_reglas.csv [--dry-run]
"""

import csv
import sys
from pathlib import Path

import yaml
import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger
from schema import RULES_COLUMNS

# Mismo significado que en apply_rules_to_sheet.py: 10 avisa de que la
# simulación encontró cambios pendientes, para que el .bat sepa si debe
# preguntar o no.
EXIT_HAY_CAMBIOS = 10


def _load_cfg() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _connect(creds_file: Path) -> gspread.Client:
    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows:
        faltan = [c for c in RULES_COLUMNS if c not in rows[0].keys()]
        if faltan:
            raise ValueError(f"Al CSV le faltan columnas: {', '.join(faltan)}")
    return rows


def _prio(r: dict) -> int:
    try:
        return int(str(r.get("priority", "")).strip() or 999999)
    except ValueError:
        return 999999


def main(csv_path: str, dry_run: bool) -> int:
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        creds_file     = _ROOT / cfg["credentials"]["gdrive_sa"]
        rules_tab      = cfg["pipeline"]["rules_tab"]
        spreadsheet_id = cfg["google_sheets"]["spreadsheet_id"]

        nuevas = _read_csv(Path(csv_path))
        if not nuevas:
            log.info("El CSV está vacío. Nada que hacer.")
            return 0
        log.info("Filas leídas del CSV: %d", len(nuevas))

        log.info("Connecting to Google Sheets...")
        gc    = _connect(creds_file)
        sheet = gc.open_by_key(spreadsheet_id).worksheet(rules_tab)

        all_values = sheet.get_all_values()
        if not all_values:
            raise ValueError("La pestaña 'rules' está vacía -- ¿tiene cabecera?")
        headers = all_values[0]
        for col in RULES_COLUMNS:
            if col not in headers:
                raise ValueError(f"Falta la columna '{col}' en la pestaña 'rules'")

        existentes = [dict(zip(headers, row)) for row in all_values[1:]]
        ids_existentes = {r.get("rule_id", "").strip()
                           for r in existentes if r.get("rule_id", "").strip()}

        # Techo real de prioridad: el cajón de sastre (match_type=exists) de
        # ESTA hoja, no la convención de 99999 de las reglas de ejemplo. Una
        # regla nueva con priority igual o mayor nunca llegaría a evaluarse
        # -- entraría en la hoja pero sería letra muerta en silencio.
        techo_prioridad = min(
            (int(r.get("priority") or 999999) for r in existentes
             if str(r.get("match_type", "")).strip().lower() == "exists"),
            default=None,
        )
        if techo_prioridad is not None:
            log.info("Techo de prioridad (cajón de sastre de esta hoja): %d", techo_prioridad)

        a_anadir: list[dict] = []
        saltadas: list[tuple[str, str]] = []
        vistos_en_csv: set[str] = set()

        for r in nuevas:
            rid = str(r.get("rule_id", "")).strip()
            mf  = str(r.get("match_field", "")).strip()
            mt  = str(r.get("match_type", "")).strip()
            cat = str(r.get("category", "")).strip()
            if not (mf and mt and cat):
                saltadas.append((rid or "(sin rule_id)",
                                  "le faltan match_field/match_type/category "
                                  "-- el motor la ignoraría en silencio"))
                continue
            if rid and rid in ids_existentes:
                saltadas.append((rid, "rule_id ya existe en la hoja"))
                continue
            if rid and rid in vistos_en_csv:
                saltadas.append((rid, "rule_id duplicado dentro del propio CSV"))
                continue
            if techo_prioridad is not None and _prio(r) >= techo_prioridad:
                saltadas.append((rid or "(sin rule_id)",
                                  f"priority={_prio(r)} no es menor que el cajón de "
                                  f"sastre ({techo_prioridad}) -- nunca se evaluaría, "
                                  f"corrige el priority en el CSV y reintenta"))
                continue
            if rid:
                vistos_en_csv.add(rid)
            a_anadir.append(r)

        log.info("Filas a añadir:  %d", len(a_anadir))
        log.info("Filas saltadas:  %d", len(saltadas))
        for rid, motivo in saltadas:
            log.info("  [saltada] %s -- %s", rid, motivo)

        if not a_anadir:
            log.info("Nada que añadir.")
            return 0

        # Resultado final: lo que ya había + lo nuevo, reordenado entero por
        # priority ascendente. No se borra ni se pisa ninguna fila existente,
        # solo se reordena -- por eso es seguro reejecutar esto.
        combinado = existentes + [
            {col: str(r.get(col, "")).strip() for col in headers} for r in a_anadir
        ]
        combinado.sort(key=_prio)

        if dry_run:
            log.info("--- DRY RUN: no se escribe nada ---")
            log.info("La hoja pasaría de %d a %d reglas.", len(existentes), len(combinado))
            for r in a_anadir:
                log.info("  [nueva] priority=%s  %s -> %s/%s",
                          r.get("priority", ""), r.get("rule_id", ""),
                          r.get("category", ""), r.get("subcategory", ""))
            log.info("Dry run complete -- nothing written.")
            return EXIT_HAY_CAMBIOS

        log.info("Escribiendo %d filas (existentes + nuevas, reordenadas)...",
                  len(combinado))
        values = [[r.get(col, "") for col in headers] for r in combinado]
        end_a1 = gspread.utils.rowcol_to_a1(len(values) + 1, len(headers))
        sheet.update(values=values, range_name=f"A2:{end_a1}", value_input_option="USER_ENTERED")

        log.info("Hecho. Reglas totales: %d (%d nuevas).", len(combinado), len(a_anadir))
        log.info("Siguiente paso: scripts\\apply_rules.bat, para recategorizar "
                  "el histórico con las reglas nuevas.")
        return 0

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        return 1


if __name__ == "__main__":
    posicionales = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if not posicionales:
        print("Uso: python pipeline/importar_reglas_csv.py <ruta_csv> [--dry-run]")
        raise SystemExit(1)
    raise SystemExit(main(posicionales[0], dry_run))
