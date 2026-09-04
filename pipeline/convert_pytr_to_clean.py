import os
import re
import json
import hashlib
import logging
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger
from pipeline.metrics import emit

CARD_PREFIXES = ("Transacción con tarjeta", "TransacciÃ³n con tarjeta")
REFUND_PREFIXES = ("Reembolso de tarjeta",)

# Trade Republic antepone esto a la nota de cada compra/reembolso con tarjeta.
# Otras fuentes de la hoja no lo hacen, así que sin recortarlo el mismo
# comercio queda como dos merchant_norm distintos según la fuente (p.ej.
# "tienda omega" vs. "transaccin con tarjeta - tienda omega"), lo que rompe tanto
# el motor de reglas como la agrupación por comercio del etiquetado manual.
_MERCHANT_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in CARD_PREFIXES + REFUND_PREFIXES) + r")\s*-\s*",
    re.IGNORECASE,
)

OUTPUT_COLUMNS = [
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
# Scalar helpers
# ---------------------------------------------------------------------------

def s(x) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def f(x):
    v = s(x)
    if not v:
        return None
    try:
        return float(v.replace(",", "."))
    except ValueError:
        return None


def parse_fecha(fecha: str):
    v = s(fecha)
    if not v:
        return "", "", ""
    try:
        dt = datetime.fromisoformat(v)
        return (
            dt.date().isoformat(),
            dt.replace(tzinfo=timezone.utc).isoformat(),
            dt.strftime("%Y-%m"),
        )
    except ValueError:
        return v[:10], v, v[:7]


def strip_merchant_prefix(text: str) -> str:
    """Quita el prefijo que antepone Trade Republic a compras/reembolsos con
    tarjeta ("Transacción con tarjeta - ", "Reembolso de tarjeta - "), sin
    tocar mayúsculas ni acentos. Para mostrar en pantalla; `norm_merchant`
    hace esto y además normaliza para poder comparar."""
    return _MERCHANT_PREFIX_RE.sub("", s(text))


def norm_merchant(note: str) -> str:
    note = strip_merchant_prefix(note)
    note = note.lower()
    note = re.sub(r"\s+", " ", note)
    note = re.sub(r"[^a-z0-9 \-&/\.]", "", note)
    return note.strip()


def clean_for_json(obj):
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    return obj


def make_tx_id(fecha, tipo, valor, nota, isin) -> str:
    key = "|".join([s(fecha), s(tipo), s(valor), s(nota), s(isin)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------

def infer_type(valor, tipo: str, nota: str, isin: str, account_holder_name: str = "") -> str:
    amount = f(valor)
    if amount is None:
        return "unknown"
    if amount < 0:
        if isin:
            return "buy"
        if s(nota).startswith(CARD_PREFIXES):
            return "card"
        return "transfer"
    t = s(tipo)
    if t == "Venta":
        return "sell"
    if t == "Dividendo":
        return "dividend"
    if t == "Intereses":
        return "interest"
    n = s(nota)
    if n == "Einzahlung" or (account_holder_name and account_holder_name.upper() in n.upper()):
        return "deposit"
    return "income"


# ---------------------------------------------------------------------------
# Rules engine
# ---------------------------------------------------------------------------

def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("true", "yes", "1")


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


def apply_rules(rules: list[dict], tx: dict) -> dict:
    for rule in rules:
        if _rule_matches(rule, tx):
            tx["category"]        = rule["category"]
            tx["subcategory"]     = rule["subcategory"]
            tx["rule_id"]         = rule["rule_id"]
            tx["rule_confidence"] = 1.0
            return tx
    tx["category"]        = ""
    tx["subcategory"]     = ""
    tx["rule_id"]         = ""
    tx["rule_confidence"] = 0.0
    return tx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        in_file              = cfg["pipeline"]["source_file"]
        out_dir              = cfg["pipeline"]["output_dir"]
        source               = cfg["pipeline"]["source_name"]
        account              = cfg["pipeline"]["account_name"]
        rules_tab            = cfg["pipeline"]["rules_tab"]
        creds                = _ROOT / cfg["credentials"]["gdrive_sa"]
        spreadsheet_id       = cfg["google_sheets"]["spreadsheet_id"]
        account_holder_name  = cfg["pipeline"].get("account_holder_name", "")

        os.makedirs(out_dir, exist_ok=True)

        log.info("Reading %s", in_file)
        df = pd.read_csv(in_file, sep=";", encoding="utf-8", engine="python")
        log.info("Rows read from source: %d", len(df))

        log.info("Fetching rules from Google Sheets (tab: %s)...", rules_tab)
        rules = load_rules(creds, spreadsheet_id, rules_tab)
        log.info("Rules loaded: %d active", len(rules))

        import_batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        created_at      = datetime.now(timezone.utc).isoformat()
        source_file     = os.path.basename(in_file)

        out_rows:    list[dict] = []
        seen_tx_ids: set[str]   = set()

        for _, r in df.iterrows():
            raw = clean_for_json(r.to_dict())

            fecha      = s(raw.get("Fecha"))
            tipo       = s(raw.get("Tipo"))
            valor      = raw.get("Valor")
            nota       = s(raw.get("Nota"))
            isin       = s(raw.get("ISIN"))
            cantidad   = raw.get("Cantidad")
            comisiones = raw.get("Comisiones")

            date_str, datetime_str, year_month = parse_fecha(fecha)
            amount   = f(valor)
            tx_type  = infer_type(valor, tipo, nota, isin, account_holder_name)
            tipus    = ("in" if amount > 0 else "out") if amount is not None else ""

            event_domain = "asset" if isin else "cashflow"

            if isin:
                rn: dict = {"pytr_type": tipo, "isin": isin}
                if cantidad   is not None: rn["shares"] = cantidad
                if comisiones is not None: rn["fees"]   = comisiones
                rule_notes = json.dumps(rn, ensure_ascii=False)
            else:
                rule_notes = ""

            tx_id = make_tx_id(fecha, tipo, valor, nota, isin)
            if tx_id in seen_tx_ids:
                continue
            seen_tx_ids.add(tx_id)

            row = {
                "tx_id":            tx_id,
                "source":           source,
                "source_file":      source_file,
                "import_batch_id":  import_batch_id,
                "date":             date_str,
                "datetime":         datetime_str,
                "amount":           amount,
                "currency":         "EUR",
                "merchant_raw":     nota,
                "merchant_norm":    norm_merchant(nota),
                "description":      nota,
                "category":         "",
                "subcategory":      "",
                "rule_id":          "",
                "rule_confidence":  0.0,
                "type":             tx_type,
                "account":          account,
                "status":           "posted",
                "notes":            "",
                "created_at":       created_at,
                "raw_json":         json.dumps(raw, ensure_ascii=False),
                "rule_notes":       rule_notes,
                "event_domain":     event_domain,
                "tipus":            tipus,
                "year_month":       year_month,
            }

            apply_rules(rules, row)
            out_rows.append(row)

        out      = pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)
        out_file = os.path.join(out_dir, f"transactions_clean_{import_batch_id}.csv")
        out.to_csv(out_file, index=False, encoding="utf-8")
        log.info("Output written: %s", out_file)
        log.info("Rows written: %d", len(out))

        matched   = (out["rule_confidence"] == 1.0).sum()
        unmatched = len(out) - matched
        log.info("Rules matched: %d/%d  |  unmatched: %d/%d",
                 matched, len(out), unmatched, len(out))

        emit("clean", cfg,
             source_rows     = len(df),
             rows_written    = len(out),
             rules_active    = len(rules),
             rules_matched   = int(matched),
             rules_unmatched = int(unmatched))

        if unmatched:
            top = (
                out[out["rule_confidence"] != 1.0][["type", "merchant_norm", "amount"]]
                .value_counts()
                .reset_index()
                .head(10)
            )
            log.warning("Top unmatched rows:\n%s", top.to_string(index=False))

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        sys.exit(1)


if __name__ == "__main__":
    main()
