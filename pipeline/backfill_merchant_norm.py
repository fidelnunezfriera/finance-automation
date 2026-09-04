#!/usr/bin/env python3
"""
Recalcula merchant_norm para las filas ya escritas en la hoja, a partir de su
merchant_raw, usando la norm_merchant() vigente en convert_pytr_to_clean.py.

Por qué hace falta un backfill aparte: merchant_norm sólo se calcula una vez,
al procesar un export de pytr. Un arreglo en norm_merchant() (por ejemplo,
recortar el prefijo "Transacción con tarjeta - " que Trade Republic antepone
y otras fuentes de la hoja no) no toca las filas que ya están en la hoja --
se quedan con el merchant_norm viejo hasta que se reimporten, y Trade
Republic exige OTP en cada login, así que no es repetible a demanda.

Como merchant_norm cambia, algunas filas pueden empezar a coincidir con una
regla que antes no matcheaba (p.ej. una fila de pytr "transaccin con tarjeta
- tienda omega" que ahora normaliza a "tienda omega" y coincide con la regla
que ya categorizaba filas de ese comercio venidas de otra fuente). Por eso esta pasada
recalcula merchant_norm y vuelve a aplicar las reglas en el mismo barrido:
dejar sólo merchant_norm actualizado y las categorías desincronizadas sería
peor que no tocar nada.

Sólo actualiza: merchant_norm, category, subcategory, rule_id,
rule_confidence. Nunca toca merchant_raw ni el resto de columnas.

Uso:
    python pipeline/backfill_merchant_norm.py [--dry-run]
"""

import sys
import yaml
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger
from pipeline.convert_pytr_to_clean import norm_merchant
from pipeline.apply_rules_to_sheet import load_rules, run_rules, _col_letter, _conf_norm

BATCH_SIZE  = 500
TARGET_COLS = ["merchant_norm", "category", "subcategory", "rule_id", "rule_confidence"]

# Mismo código de salida que apply_rules_to_sheet.py, por la misma razón: el
# .bat necesita distinguir "hay cambios" de "algo falló" con `if errorlevel`.
EXIT_HAY_CAMBIOS = 10


def _load_cfg() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _connect(creds_file: Path) -> gspread.Client:
    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def _needs_update(row: dict, new_norm: str,
                   new_cat: str, new_sub: str, new_rid: str, new_conf: float) -> bool:
    return (
        row.get("merchant_norm", "").strip() != new_norm
        or row.get("category",       "").strip() != new_cat
        or row.get("subcategory",    "").strip() != new_sub
        or row.get("rule_id",        "").strip() != new_rid
        or _conf_norm(row.get("rule_confidence", "")) != _conf_norm(new_conf)
    )


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

        for col in TARGET_COLS + ["merchant_raw"]:
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

            new_norm = norm_merchant(row.get("merchant_raw", ""))
            tx = dict(row)
            tx["merchant_norm"] = new_norm
            new_cat, new_sub, new_rid, new_conf = run_rules(rules, tx)

            if _needs_update(row, new_norm, new_cat, new_sub, new_rid, new_conf):
                update_rows.append((
                    i, new_norm, new_cat, new_sub, new_rid, new_conf,
                    row.get("merchant_norm", ""), row.get("category", ""),
                ))
            else:
                skipped += 1

        total = len(all_values) - 1
        log.info("Total rows processed: %d", total)
        log.info("Rows to update:       %d", len(update_rows))
        log.info("Rows already correct: %d", skipped)

        # Distingue cuántas filas cambian de merchant_norm (el bug de raíz) de
        # cuántas, como consecuencia, cambian también de categoría.
        cambia_norm = sum(1 for u in update_rows if u[6].strip() != u[1])
        cambia_cat  = sum(1 for u in update_rows if u[7].strip() != u[2])
        log.info("  ... of which change merchant_norm: %d", cambia_norm)
        log.info("  ... of which change category:      %d", cambia_cat)

        if dry_run and not update_rows:
            log.info("Nothing to update -- merchant_norm already matches merchant_raw.")
            return 0

        if dry_run:
            sample = update_rows[:15]
            log.info("--- DRY RUN: first %d rows that would be updated ---", len(sample))
            for (sheet_row, new_norm, new_cat, new_sub, new_rid, new_conf,
                 old_norm, old_cat) in sample:
                cambios = [f"merchant_norm: {old_norm!r} -> {new_norm!r}"]
                if old_cat.strip() != new_cat:
                    cambios.append(f"category: {old_cat!r} -> {new_cat!r}")
                log.info("  [row %d] %s", sheet_row, "  ".join(cambios))
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
            for sheet_row, new_norm, new_cat, new_sub, new_rid, new_conf, *_ in update_rows:
                data.append({
                    "range":  f"{range_start}{sheet_row}:{range_end}{sheet_row}",
                    "values": [[new_norm, new_cat, new_sub, new_rid, new_conf]],
                })
        else:
            for sheet_row, new_norm, new_cat, new_sub, new_rid, new_conf, *_ in update_rows:
                for letter, val in zip(target_letters,
                                       [new_norm, new_cat, new_sub, new_rid, new_conf]):
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
