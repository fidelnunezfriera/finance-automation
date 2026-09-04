#!/usr/bin/env python3
"""
Derive portfolio positions from the transactions Google Sheet tab.

Reads transactions where event_domain == 'asset', groups by isin + merchant_raw,
sums shares from the rule_notes JSON field, and writes a snapshot to the
positions tab.

Usage:
    python pipeline/derive_positions.py
"""

import json
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger
from pipeline.metrics import emit
from schema import POSITIONS_COLUMNS

POSITIONS_TAB  = "positions"
EPSILON        = 1e-4

# pytr_type values that reduce the position (shares must be negated)
SELL_TYPES = {"venta"}

# pytr_type values that do not affect share count (cash events: dividends, interest, etc.)
IGNORE_TYPES = {"dividendo"}

# Epsilon thresholds mirrored from the old CSV-based script.
# Equities accumulate tiny residuals from fractional share rounding; 0.1 absorbs them.
# Crypto uses near-zero epsilon because sub-unit amounts are real holdings.
# Bonds (XS*) use exact comparison — nominal value is always an integer.
EPSILON_EQUITY  = 0.1
EPSILON_CRYPTO  = 1e-6

COLUMNS = POSITIONS_COLUMNS  # ver schema.py


def _load_cfg() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _connect(creds_file: Path) -> gspread.Client:
    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def _parse_rule_notes(rule_notes: str) -> tuple[str, float]:
    """Return (isin, signed_shares) from rule_notes JSON.

    Shares are negated for sell-type transactions (pytr_type in SELL_TYPES).
    Returns ("", 0.0) if the JSON is missing or unparseable.
    """
    if not rule_notes or not rule_notes.strip():
        return "", 0.0
    try:
        data = json.loads(rule_notes)
        isin = str(data.get("isin", "")).strip()
        pytr_type = str(data.get("pytr_type", "")).strip().lower()
        if pytr_type in IGNORE_TYPES:
            return isin, 0.0
        for key in ("shares", "Cantidad", "cantidad"):
            if key in data:
                shares = float(data[key])
                if pytr_type in SELL_TYPES:
                    shares = -shares
                return isin, shares
        return isin, 0.0
    except (json.JSONDecodeError, ValueError, TypeError):
        return "", 0.0


def main() -> None:
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        creds_file     = _ROOT / cfg["credentials"]["gdrive_sa"]
        sheet_name     = cfg["pipeline"]["sheet_name"]
        spreadsheet_id = cfg["google_sheets"]["spreadsheet_id"]

        log.info("Connecting to Google Sheets (id=%s)...", spreadsheet_id)
        client      = _connect(creds_file)
        spreadsheet = client.open_by_key(spreadsheet_id)

        log.info("Reading tab: %s", sheet_name)
        tx_sheet = spreadsheet.worksheet(sheet_name)
        records  = tx_sheet.get_all_records()
        log.info("Transactions read: %d rows", len(records))

        asset_rows = [
            r for r in records
            if str(r.get("event_domain", "")).strip().lower() == "asset"
        ]
        log.info("Asset rows (event_domain='asset'): %d", len(asset_rows))

        # Group by (isin, merchant_raw) and accumulate signed shares from rule_notes JSON.
        # isin lives inside rule_notes JSON, not as a top-level column.
        # Sells are stored with positive shares in rule_notes; _parse_rule_notes negates them.
        groups: dict[tuple, dict] = {}
        skipped_no_isin = 0
        for row in asset_rows:
            isin, shares = _parse_rule_notes(str(row.get("rule_notes", "")))
            if not isin:
                skipped_no_isin += 1
                continue
            name = str(row.get("merchant_raw", "")).strip()
            key  = (isin, name)
            if key not in groups:
                groups[key] = {"isin": isin, "name": name, "quantity": 0.0}
            groups[key]["quantity"] += shares

        if skipped_no_isin:
            log.warning("Skipped %d asset rows with no isin in rule_notes.", skipped_no_isin)

        log.info("Unique (isin, name) groups: %d", len(groups))

        snapshot_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        positions: list[dict] = []

        for g in groups.values():
            isin = g["isin"]
            qty  = g["quantity"]

            if isin.startswith("XS"):
                # Bonds: nominal is always integer-ish, use exact comparison
                status   = "open" if qty != 0 else "closed"
                adjusted = False
                anomaly  = qty < 0
            else:
                eps = EPSILON_CRYPTO if isin.startswith("XF000") else EPSILON_EQUITY
                if abs(qty) < eps:
                    qty      = 0.0
                    status   = "closed"
                    adjusted = True
                    anomaly  = False
                elif qty < 0:
                    status   = "closed"
                    adjusted = False
                    anomaly  = True
                else:
                    status   = "open"
                    adjusted = False
                    anomaly  = False

            positions.append({
                "snapshot_at": snapshot_at,
                "isin":        isin,
                "name":        g["name"],
                "quantity":    round(qty, 6),
                "status":      status,
                "adjusted":    "TRUE" if adjusted else "FALSE",
                "anomaly":     "TRUE" if anomaly  else "FALSE",
            })

        # Sort: open first, then alphabetically by name
        positions.sort(key=lambda p: (0 if p["status"] == "open" else 1, p["name"].lower()))

        # Clear positions tab and rewrite
        log.info("Writing %d rows to tab '%s'...", len(positions), POSITIONS_TAB)
        try:
            pos_sheet = spreadsheet.worksheet(POSITIONS_TAB)
        except gspread.exceptions.WorksheetNotFound:
            log.info("Tab '%s' not found — creating it.", POSITIONS_TAB)
            pos_sheet = spreadsheet.add_worksheet(
                title=POSITIONS_TAB, rows=1000, cols=len(COLUMNS)
            )

        pos_sheet.clear()
        rows = [COLUMNS] + [
            [
                p["snapshot_at"],
                p["isin"],
                p["name"],
                p["quantity"],
                p["status"],
                p["adjusted"],
                p["anomaly"],
            ]
            for p in positions
        ]
        pos_sheet.update(rows, value_input_option="USER_ENTERED")
        log.info("Positions tab updated successfully.")

        # Print results table
        header = f"{'ISIN':<15}  {'NAME':<40}  {'QTY':>12}  {'STATUS':<8}  ANOMALY"
        sep    = "-" * len(header)
        print(f"\n{header}\n{sep}")
        anomaly_count = 0
        for p in positions:
            flag = "  *** ANOMALY ***" if p["anomaly"] == "TRUE" else ""
            print(
                f"{p['isin']:<15}  {p['name'][:40]:<40}  {p['quantity']:>12.6f}"
                f"  {p['status']:<8}  {p['anomaly']}{flag}"
            )
            if p["anomaly"] == "TRUE":
                anomaly_count += 1

        print()
        n_open   = sum(1 for p in positions if p["status"] == "open")
        n_closed = sum(1 for p in positions if p["status"] == "closed")

        log.info("Snapshot:        %s", snapshot_at)
        log.info(
            "Total positions: %d  (open: %d  closed: %d  anomalies: %d)",
            len(positions), n_open, n_closed, anomaly_count,
        )

        emit("positions", cfg,
             positions_total     = len(positions),
             positions_open      = n_open,
             positions_closed    = n_closed,
             positions_anomalies = anomaly_count)

        if anomaly_count:
            log.warning("%d anomalous position(s) detected (negative net quantity).", anomaly_count)

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        sys.exit(1)


if __name__ == "__main__":
    main()
