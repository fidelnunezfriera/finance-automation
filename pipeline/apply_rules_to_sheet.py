#!/usr/bin/env python3
"""
Retroactively apply the rules engine to all rows in the transactions sheet.

Only updates: category, subcategory, rule_id, rule_confidence.
Never touches: notes or any other column.
Skips rows whose values already match -- only writes diffs.

Usage:
    python pipeline/apply_rules_to_sheet.py [--dry-run]
"""

import logging
import re
import sys
import yaml
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger

BATCH_SIZE = 500
TARGET_COLS    = ["category", "subcategory", "rule_id", "rule_confidence"]


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


def _col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("true", "yes", "1")


def _conf_norm(v) -> float:
    try:
        return round(float(str(v).strip()), 6) if str(v).strip() else 0.0
    except ValueError:
        return 0.0


def _needs_update(row: dict,
                  new_cat: str, new_sub: str,
                  new_rid: str, new_conf: float) -> bool:
    return (
        row.get("category",       "").strip() != new_cat
        or row.get("subcategory", "").strip() != new_sub
        or row.get("rule_id",     "").strip() != new_rid
        or _conf_norm(row.get("rule_confidence", "")) != _conf_norm(new_conf)
    )


# ---------------------------------------------------------------------------
# Rules engine  (identical logic to convert_pytr_to_clean.py)
# ---------------------------------------------------------------------------

def load_rules(creds_file: Path, spreadsheet_id: str, rules_tab: str) -> list[dict]:
    creds   = Credentials.from_service_account_file(
        str(creds_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc      = gspread.authorize(creds)
    records = gc.open_by_key(spreadsheet_id).worksheet(rules_tab).get_all_records()

    seen:  set[str]   = set()
    rules: list[dict] = []
    for r in records:
        if not _to_bool(r.get("enabled", True)):
            continue
        mf  = str(r.get("match_field",  "")).strip()
        mt  = str(r.get("match_type",   "")).strip().lower()
        mv  = str(r.get("match_value",  ""))
        cat = str(r.get("category",     "")).strip()
        if not (mf and mt and cat):
            continue
        rid       = str(r.get("rule_id",        "")).strip()
        sub       = str(r.get("subcategory",     "")).strip()
        applies   = str(r.get("applies_to_type", "")).strip()
        direction = str(r.get("direction",       "")).strip().lower()
        key = rid or f"{mf}|{mt}|{mv}|{cat}|{sub}|{applies}|{direction}|{r.get('priority','')}"
        if key in seen:
            continue
        seen.add(key)
        rules.append({
            "rule_id":         rid,
            "priority":        int(r.get("priority") or 999999),
            "match_field":     mf,
            "match_type":      mt,
            "match_value":     mv,
            "category":        cat,
            "subcategory":     sub,
            "applies_to_type": applies,
            "direction":       direction,
        })
    rules.sort(key=lambda r: r["priority"])
    return rules


def _rule_matches(rule: dict, tx: dict) -> bool:
    applies   = rule["applies_to_type"]
    direction = rule["direction"]
    if applies   and tx.get("type",  "") != applies:
        return False
    if direction and tx.get("tipus", "") != direction:
        return False
    field = rule["match_field"]
    mtype = rule["match_type"]
    value = rule["match_value"]
    if mtype == "exists":
        v = tx.get(field, "")
        return v is not None and str(v).strip() != ""
    hay = str(tx.get(field, "")).strip()
    if not hay:
        return False
    if mtype == "equals":
        return hay.lower() == value.strip().lower()
    if mtype == "contains":
        return value.strip().lower() in hay.lower()
    if mtype == "regex":
        try:
            return bool(re.search(value, hay, re.IGNORECASE))
        except re.error:
            return False
    return False


def run_rules(rules: list[dict], tx: dict) -> tuple[str, str, str, float]:
    for rule in rules:
        if _rule_matches(rule, tx):
            return rule["category"], rule["subcategory"], rule["rule_id"], 1.0
    return "", "", "", 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Código de salida con el que una simulación avisa de que SÍ hay cambios
# pendientes. Se eligió un valor alto y distinto de 1 para no confundirlo con
# un fallo: `if errorlevel` en un .bat compara "mayor o igual que", así que los
# códigos de error (1) y este tienen que quedar bien separados.
#
#   0   la simulación fue bien y no hay nada que actualizar
#   10  la simulación fue bien y hay cambios pendientes
#   1   algo falló
EXIT_HAY_CAMBIOS = 10


def main(dry_run: bool = False) -> int:
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        creds_file     = _ROOT / cfg["credentials"]["gdrive_sa"]
        sheet_name     = cfg["pipeline"]["sheet_name"]
        rules_tab      = cfg["pipeline"]["rules_tab"]
        spreadsheet_id = cfg["google_sheets"]["spreadsheet_id"]

        log.info("Connecting to Google Sheets...")
        client      = _connect(creds_file)
        spreadsheet = client.open_by_key(spreadsheet_id)
        sheet       = spreadsheet.worksheet(sheet_name)

        log.info("Fetching rules (tab: %s)...", rules_tab)
        rules = load_rules(creds_file, spreadsheet_id, rules_tab)
        log.info("Rules loaded: %d active", len(rules))

        log.info("Reading sheet (tab: %s)...", sheet_name)
        all_values = sheet.get_all_values()
        if not all_values:
            raise ValueError("Sheet is empty")

        headers = all_values[0]
        n_cols  = len(headers)

        for col in TARGET_COLS:
            if col not in headers:
                raise ValueError(f"Column '{col}' not found in sheet header")

        target_indices = [headers.index(col) for col in TARGET_COLS]
        consecutive = target_indices == list(range(target_indices[0],
                                                   target_indices[0] + len(TARGET_COLS)))
        if consecutive:
            range_start = _col_letter(target_indices[0] + 1)
            range_end   = _col_letter(target_indices[-1] + 1)
        else:
            target_letters = [_col_letter(i + 1) for i in target_indices]

        update_rows: list[tuple] = []
        skipped = 0

        for i, row_vals in enumerate(all_values[1:], start=2):
            padded = list(row_vals) + [""] * (n_cols - len(row_vals))
            row    = dict(zip(headers, padded))
            new_cat, new_sub, new_rid, new_conf = run_rules(rules, row)
            if _needs_update(row, new_cat, new_sub, new_rid, new_conf):
                update_rows.append((
                    i, new_cat, new_sub, new_rid, new_conf,
                    row.get("category", ""), row.get("subcategory", ""),
                    row.get("rule_id", ""), row.get("merchant_norm", ""),
                ))
            else:
                skipped += 1

        total = len(all_values) - 1
        log.info("Total rows processed: %d", total)
        log.info("Rows to update:       %d", len(update_rows))
        log.info("Rows already correct: %d", skipped)

        # Cuántas filas cambian de categoría de verdad y cuántas sólo cambian
        # de regla atribuida. Sin esta distinción, "2862 filas a actualizar"
        # asusta lo mismo si son recategorizaciones reales que si es la misma
        # categoría llegando por otra regla.
        cambian_cat = sum(1 for u in update_rows
                          if u[5].strip() != u[1] or u[6].strip() != u[2])
        log.info("  ... of which change category/subcategory: %d", cambian_cat)
        log.info("  ... only reattributed to another rule:    %d",
                 len(update_rows) - cambian_cat)

        if dry_run and not update_rows:
            log.info("Nothing to update -- the sheet already matches the rules.")
            return 0

        if dry_run:
            sample = update_rows[:15]
            log.info("--- DRY RUN: first %d rows that would be updated ---", len(sample))
            for (sheet_row, new_cat, new_sub, new_rid, new_conf,
                 old_cat, old_sub, old_rid, merchant) in sample:
                # Sólo se listan los campos que cambian: repetir los iguales
                # hacía parecer que la fila no cambiaba nada.
                cambios = []
                if old_cat.strip() != new_cat:
                    cambios.append(f"category: {old_cat!r} -> {new_cat!r}")
                if old_sub.strip() != new_sub:
                    cambios.append(f"subcategory: {old_sub!r} -> {new_sub!r}")
                if old_rid.strip() != new_rid:
                    cambios.append(f"rule_id: {old_rid!r} -> {new_rid!r}")
                log.info("  [row %d] %s | %s", sheet_row,
                         (merchant or "(sin comercio)")[:28], "  ".join(cambios))
            if len(update_rows) > len(sample):
                log.info("  ... and %d more", len(update_rows) - len(sample))
            log.info("Dry run complete -- nothing written.")
            return EXIT_HAY_CAMBIOS

        if not update_rows:
            log.info("Nothing to update.")
            return 0

        log.info("Building batch update...")
        data: list[dict] = []

        if consecutive:
            for sheet_row, new_cat, new_sub, new_rid, new_conf, *_ in update_rows:
                data.append({
                    "range":  f"{range_start}{sheet_row}:{range_end}{sheet_row}",
                    "values": [[new_cat, new_sub, new_rid, new_conf]],
                })
        else:
            for sheet_row, new_cat, new_sub, new_rid, new_conf, *_ in update_rows:
                for letter, val in zip(target_letters,
                                       [new_cat, new_sub, new_rid, new_conf]):
                    data.append({"range": f"{letter}{sheet_row}", "values": [[val]]})

        log.info("Sending %d range updates in chunks of %d...", len(data), BATCH_SIZE)
        for i in range(0, len(data), BATCH_SIZE):
            chunk = data[i : i + BATCH_SIZE]
            sheet.batch_update(chunk, value_input_option="USER_ENTERED")
            log.info("  %d/%d updates sent", min(i + BATCH_SIZE, len(data)), len(data))

        log.info("Rows updated:        %d", len(update_rows))
        log.info("Rows skipped:        %d", skipped)
        log.info("Total rows in sheet: %d", total)
        return 0

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        sys.exit(1)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    raise SystemExit(main(dry_run=dry_run))
