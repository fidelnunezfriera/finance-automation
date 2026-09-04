#!/usr/bin/env python3
"""
Upsert cleaned TR transactions into the Google Sheets 'transactions' tab.

  - Match on tx_id: existing rows are overwritten, new rows are appended.
  - After upsert the sheet is sorted by datetime descending (header fixed).
  - Rows not present in the CSV (e.g. rows added through other means) are never touched.
  - NaN / None values are written as empty string.

Usage:
    python sheets/push_to_sheets.py [--dry-run] [path/to/transactions_clean_*.csv]
    python sheets/push_to_sheets.py --delete-batch BATCH_ID
"""

import csv
import glob
import logging
import os
import sys
import yaml
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger
from pipeline.metrics import emit

BATCH_SIZE = 500

COLUMNS = [
    "tx_id", "source", "source_file", "import_batch_id",
    "date", "datetime", "amount", "currency",
    "merchant_raw", "merchant_norm", "description",
    "category", "subcategory", "rule_id", "rule_confidence",
    "type", "account", "status", "notes", "created_at",
    "raw_json", "rule_notes", "event_domain", "tipus", "year_month",
]


def _load_cfg() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connect(creds_file: Path) -> gspread.Client:
    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def _clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _newest_csv(output_dir: str) -> str:
    pattern = str(_ROOT / output_dir / "transactions_clean_*.csv")
    files   = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(f"No transactions_clean_*.csv found in {output_dir}/")
    return max(files, key=os.path.getmtime)


def _load_csv(csv_path: str) -> list[dict]:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({k: _clean(v) for k, v in row.items()})
    return rows


# Columnas que deben llegar a Sheets como número, no como texto.
#
# Con value_input_option="USER_ENTERED" Google interpreta cada *cadena* según
# la configuración regional de la hoja. En una hoja es_ES el punto es separador
# de miles, así que "177.5148" participaciones se guardaban como 1.775.148 y
# "1.234" euros como 1234. En una hoja es_MX o en_US el mismo texto entra bien,
# de ahí que el fallo sólo apareciese en algunas hojas.
#
# Un número de verdad viaja como número en el JSON de la API y no pasa por ese
# intérprete, así que el resultado es idéntico en cualquier idioma.
NUMERIC_COLUMNS = {"amount", "rule_confidence"}


def _as_number(value: str):
    """Convierte a float si la columna es numérica; si no, deja el texto.

    Un valor vacío o no numérico se devuelve tal cual: es preferible que una
    celda rara se vea como texto a inventarse un cero.
    """
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return float(text)
    except ValueError:
        return value


def _row_values(tx: dict, headers: list[str]) -> list:
    return [
        _as_number(_clean(tx.get(col, ""))) if col in NUMERIC_COLUMNS
        else _clean(tx.get(col, ""))
        for col in headers
    ]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def push(csv_path: str, cfg: dict, log: logging.Logger, dry_run: bool = False) -> None:
    creds_file     = _ROOT / cfg["credentials"]["gdrive_sa"]
    sheet_name     = cfg["pipeline"]["sheet_name"]
    spreadsheet_id = cfg["google_sheets"]["spreadsheet_id"]

    log.info("CSV: %s", csv_path)
    csv_rows = _load_csv(csv_path)
    log.info("Rows loaded from CSV: %d", len(csv_rows))

    log.info("Connecting to Google Sheets...")
    client      = _connect(creds_file)
    spreadsheet = client.open_by_key(spreadsheet_id)
    sheet       = spreadsheet.worksheet(sheet_name)

    log.info("Reading sheet (tab: %s)...", sheet_name)
    all_values = sheet.get_all_values()
    if not all_values:
        raise ValueError("Sheet is empty — no header row found")

    sheet_headers = all_values[0]
    n_cols        = len(sheet_headers)
    last_col      = _col_letter(n_cols)

    if "tx_id" not in sheet_headers:
        raise ValueError("Sheet has no 'tx_id' column")
    tx_id_col = sheet_headers.index("tx_id")

    existing: dict[str, int] = {}
    for i, row in enumerate(all_values[1:], start=2):
        tid = row[tx_id_col] if tx_id_col < len(row) else ""
        if tid:
            existing[tid] = i

    existing_total = len(all_values) - 1
    log.info("Existing rows in sheet: %d (%d with tx_id)", existing_total, len(existing))

    to_update: list[tuple[int, list[str]]] = []
    to_insert: list[list[str]]             = []

    for tx in csv_rows:
        tx_id  = tx.get("tx_id", "")
        values = _row_values(tx, sheet_headers)
        if tx_id in existing:
            to_update.append((existing[tx_id], values))
        else:
            to_insert.append(values)

    log.info("Rows to update (existing tx_id): %d", len(to_update))
    log.info("Rows to insert (new tx_id):      %d", len(to_insert))

    if dry_run:
        sample = (
            [("UPDATE", rn, v) for rn, v in to_update]
            + [("INSERT", None, v) for v  in to_insert]
        )[:5]
        preview_cols = ["tx_id", "date", "amount", "merchant_raw",
                        "type", "category", "subcategory", "event_domain", "tipus"]
        log.info("--- DRY RUN: first 5 rows ---")
        for op, row_num, vals in sample:
            row_dict = dict(zip(sheet_headers, vals))
            loc      = f"sheet row {row_num}" if row_num else "new row"
            log.info("  [%s -- %s]", op, loc)
            for col in preview_cols:
                log.info("    %-22s = %r", col, row_dict.get(col, ""))
        log.info("Dry run complete -- nothing written.")
        return

    if to_update:
        log.info("Batch-updating %d existing rows...", len(to_update))
        data = [
            {"range": f"A{row_num}:{last_col}{row_num}", "values": [vals]}
            for row_num, vals in to_update
        ]
        for i in range(0, len(data), BATCH_SIZE):
            chunk = data[i : i + BATCH_SIZE]
            sheet.batch_update(chunk, value_input_option="USER_ENTERED")
            log.info("  Updated rows %d-%d", i + 1, i + len(chunk))

    if to_insert:
        log.info("Appending %d new rows...", len(to_insert))
        for i in range(0, len(to_insert), BATCH_SIZE):
            chunk = to_insert[i : i + BATCH_SIZE]
            sheet.append_rows(chunk, value_input_option="USER_ENTERED")
            log.info("  Appended rows %d-%d", i + 1, i + len(chunk))

    log.info("Sorting sheet by datetime descending...")
    datetime_col_index = (
        sheet_headers.index("datetime") if "datetime" in sheet_headers else 5
    )
    spreadsheet.batch_update({
        "requests": [{
            "sortRange": {
                "range": {
                    "sheetId":          sheet.id,
                    "startRowIndex":    1,
                    "startColumnIndex": 0,
                    "endColumnIndex":   n_cols,
                },
                "sortSpecs": [{
                    "dimensionIndex": datetime_col_index,
                    "sortOrder":      "DESCENDING",
                }],
            }
        }]
    })

    final_total = existing_total + len(to_insert)
    log.info("Rows updated:   %d", len(to_update))
    log.info("Rows inserted:  %d", len(to_insert))
    log.info("Total in sheet: %d", final_total)

    emit("push", cfg,
         push_rows_loaded   = len(csv_rows),
         push_rows_updated  = len(to_update),
         push_rows_inserted = len(to_insert),
         sheet_total        = final_total)


def delete_batch(batch_id: str, cfg: dict, log: logging.Logger) -> None:
    creds_file     = _ROOT / cfg["credentials"]["gdrive_sa"]
    sheet_name     = cfg["pipeline"]["sheet_name"]
    spreadsheet_id = cfg["google_sheets"]["spreadsheet_id"]

    client      = _connect(creds_file)
    spreadsheet = client.open_by_key(spreadsheet_id)
    sheet       = spreadsheet.worksheet(sheet_name)

    log.info("Reading sheet...")
    all_values = sheet.get_all_values()
    headers    = all_values[0]
    if "import_batch_id" not in headers:
        log.error("import_batch_id column not found")
        sys.exit(1)

    batch_col = headers.index("import_batch_id")
    to_delete = [
        i + 2
        for i, row in enumerate(all_values[1:])
        if (row[batch_col] if batch_col < len(row) else "") == batch_id
    ]

    if not to_delete:
        log.info("No rows found with import_batch_id=%r", batch_id)
        return

    log.info("Deleting %d rows...", len(to_delete))
    requests = [
        {"deleteDimension": {"range": {
            "sheetId":    sheet.id,
            "dimension":  "ROWS",
            "startIndex": r - 1,
            "endIndex":   r,
        }}}
        for r in sorted(to_delete, reverse=True)
    ]
    for i in range(0, len(requests), BATCH_SIZE):
        spreadsheet.batch_update({"requests": requests[i : i + BATCH_SIZE]})
        log.info("  Deleted %d/%d", min(i + BATCH_SIZE, len(requests)), len(requests))
    log.info("Done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        args    = sys.argv[1:]
        dry_run = "--dry-run" in args
        args    = [a for a in args if a != "--dry-run"]

        if args and args[0] == "--delete-batch":
            if len(args) != 2:
                log.error("Usage: push_to_sheets.py --delete-batch BATCH_ID")
                sys.exit(1)
            delete_batch(args[1], cfg, log)
        else:
            csv_path = args[0] if args else _newest_csv(cfg["pipeline"]["output_dir"])
            push(csv_path, cfg, log, dry_run=dry_run)

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        sys.exit(1)
