#!/usr/bin/env python3
"""
Exporta los comercios (merchant_norm) del histórico propio que ninguna regla
"real" categoriza todavía -- solo caen en la regla de respaldo
(match_type=exists) o no coinciden con nada.

Pensado para el arranque en frío: un clon nuevo del repositorio solo trae
las reglas de ejemplo (`ejemplo-*` de schema.py), que categorizan una
fracción mínima de las transacciones de un usuario real. Este script genera
el insumo para pedirle a un LLM que proponga reglas nuevas a partir de los
propios comercios -- ver docs/GENERAR_REGLAS.md para el flujo completo.

Solo lee las pestañas `rules` y `transactions`: no escribe nada en la hoja.
Escribe dos CSV locales en out/ (gitignored).

Uso:
    python pipeline/exportar_comercios_sin_regla.py
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
import gspread
from google.oauth2.service_account import Credentials

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger


def _load_cfg() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _connect(creds_file: Path) -> gspread.Client:
    creds = Credentials.from_service_account_file(
        str(creds_file), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("true", "yes", "1")


# ---------------------------------------------------------------------------
# Motor de reglas -- misma lógica que convert_pytr_to_clean.py /
# apply_rules_to_sheet.py, duplicada a propósito en vez de importada: son
# scripts standalone independientes, y una única fuente compartida
# obligaría a acoplarlos.
# ---------------------------------------------------------------------------

def load_rules(gc: gspread.Client, spreadsheet_id: str, rules_tab: str) -> list[dict]:
    records = gc.open_by_key(spreadsheet_id).worksheet(rules_tab).get_all_records()
    seen: set[str] = set()
    rules: list[dict] = []
    for r in records:
        if not _to_bool(r.get("enabled", True)):
            continue
        mf  = str(r.get("match_field", "")).strip()
        mt  = str(r.get("match_type", "")).strip().lower()
        mv  = str(r.get("match_value", ""))
        cat = str(r.get("category", "")).strip()
        if not (mf and mt and cat):
            continue
        rid       = str(r.get("rule_id", "")).strip()
        sub       = str(r.get("subcategory", "")).strip()
        applies   = str(r.get("applies_to_type", "")).strip()
        direction = str(r.get("direction", "")).strip().lower()
        key = rid or f"{mf}|{mt}|{mv}|{cat}|{sub}|{applies}|{direction}|{r.get('priority', '')}"
        if key in seen:
            continue
        seen.add(key)
        rules.append({
            "rule_id": rid, "priority": int(r.get("priority") or 999999),
            "match_field": mf, "match_type": mt, "match_value": mv,
            "category": cat, "subcategory": sub,
            "applies_to_type": applies, "direction": direction,
        })
    rules.sort(key=lambda r: r["priority"])
    return rules


def _rule_matches(rule: dict, tx: dict) -> bool:
    applies   = rule["applies_to_type"]
    direction = rule["direction"]
    if applies and tx.get("type", "") != applies:
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


def tiene_regla_real(reglas_sin_catchall: list[dict], tx: dict) -> bool:
    return any(_rule_matches(r, tx) for r in reglas_sin_catchall)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        creds_file     = _ROOT / cfg["credentials"]["gdrive_sa"]
        sheet_name     = cfg["pipeline"]["sheet_name"]
        rules_tab      = cfg["pipeline"]["rules_tab"]
        spreadsheet_id = cfg["google_sheets"]["spreadsheet_id"]
        account_holder = str(cfg["pipeline"].get("account_holder_name", "")).strip()

        log.info("Connecting to Google Sheets...")
        gc = _connect(creds_file)

        log.info("Fetching rules (tab: %s)...", rules_tab)
        rules = load_rules(gc, spreadsheet_id, rules_tab)
        # Las reglas "exists" son cajones de sastre (match_type=exists) --
        # "coinciden" con cualquier cosa, así que no cuentan como regla real
        # a la hora de decidir qué comercio necesita una regla nueva.
        cajones_de_sastre = [r for r in rules if r["match_type"] == "exists"]
        reglas_reales     = [r for r in rules if r["match_type"] != "exists"]
        log.info("Rules loaded: %d active, %d 'real' (sin contar cajones de sastre)",
                  len(rules), len(reglas_reales))

        # El techo de prioridad que cualquier regla nueva tiene que respetar
        # para llegar a evaluarse -- NO asumir 99999 (la convención de las
        # reglas de ejemplo en schema.py). Una hoja con historial propio
        # puede tener su cajón de sastre en cualquier número: en una prueba
        # real se encontró en 999, y una regla nueva con priority=8780
        # nunca llegaba a evaluarse porque el cajón de sastre ganaba antes.
        techo_prioridad = min((r["priority"] for r in cajones_de_sastre), default=None)
        if techo_prioridad is not None:
            log.info("Cajón de sastre detectado: %s, priority=%d -- "
                      "las reglas nuevas deben quedar por debajo de eso",
                      ", ".join(r["rule_id"] or "(sin id)" for r in cajones_de_sastre
                                if r["priority"] == techo_prioridad),
                      techo_prioridad)
        else:
            log.info("No se detectó ningún cajón de sastre (match_type=exists) activo.")

        log.info("Reading sheet (tab: %s)...", sheet_name)
        records = gc.open_by_key(spreadsheet_id).worksheet(sheet_name).get_all_records()
        log.info("Transactions read: %d", len(records))

        counts: Counter = Counter()
        tipos: "defaultdict[str, Counter]" = defaultdict(Counter)
        direcciones: "defaultdict[str, Counter]" = defaultdict(Counter)

        for tx in records:
            merchant = str(tx.get("merchant_norm", "")).strip()
            if not merchant:
                continue
            if tiene_regla_real(reglas_reales, tx):
                continue
            counts[merchant] += 1
            tipos[merchant][str(tx.get("type", "")).strip().lower() or "(vacío)"] += 1
            # in/out normalizado a minúsculas: la columna tipus tiene
            # mayúsculas inconsistentes en filas antiguas de la hoja, y sin
            # normalizar "OUT" y "out" contarían como direcciones distintas.
            direcciones[merchant][str(tx.get("tipus", "")).strip().lower() or "(ambas)"] += 1

        if not counts:
            log.info("Todos los comercios ya tienen una regla real. Nada que exportar.")
            return 0

        # El nombre del titular de la cuenta puede aparecer en un
        # merchant_norm de traspaso a cuenta propia ("Transferencia a mí
        # mismo"). No tiene sentido pegar un nombre real en el prompt de un
        # LLM externo.
        def _redact(m: str) -> str:
            if account_holder and account_holder.lower() in m.lower():
                return "[mi nombre]"
            return m

        out_dir = _ROOT / "out"
        out_dir.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Ordenados por frecuencia: al pegar en un LLM con contexto
        # limitado, interesa priorizar los comercios que cubren más
        # transacciones, no la lista completa en orden arbitrario.
        rows = sorted(counts.items(), key=lambda kv: -kv[1])
        total_tx_sin_regla = sum(counts.values())

        comercios_file = out_dir / f"comercios_sin_regla_{ts}.csv"
        with open(comercios_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["merchant_norm", "apariciones", "tipo_dominante", "direccion_dominante"])
            for merchant, n in rows:
                tipo_dom = tipos[merchant].most_common(1)[0][0]
                dir_dom  = direcciones[merchant].most_common(1)[0][0]
                w.writerow([_redact(merchant), n, tipo_dom, dir_dom])

        # Taxonomía ya en uso, para que el LLM reutilice categorías y
        # subcategorías existentes en vez de inventar unas nuevas.
        taxonomia = sorted({(r["category"], r["subcategory"]) for r in reglas_reales})
        taxonomia_file = out_dir / f"taxonomia_actual_{ts}.csv"
        with open(taxonomia_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["category", "subcategory"])
            for cat, sub in taxonomia:
                w.writerow([cat, sub])

        # El techo de prioridad en su propio fichero, en texto plano y listo
        # para pegar en el prompt -- así el número real de esta hoja
        # sustituye a cualquier rango fijo que se le sugiera al LLM.
        limite_file = out_dir / f"limite_prioridad_{ts}.txt"
        with open(limite_file, "w", encoding="utf-8") as f:
            if techo_prioridad is not None:
                f.write(
                    f"Las reglas nuevas deben usar un priority MENOR que "
                    f"{techo_prioridad} (el cajón de sastre de esta hoja está "
                    f"en priority={techo_prioridad}; una regla con priority "
                    f"mayor o igual nunca se llegaría a evaluar).\n"
                )
            else:
                f.write(
                    "No se detectó cajón de sastre activo en esta hoja. "
                    "Usa cualquier priority libre, dejando huecos para poder "
                    "intercalar reglas después.\n"
                )

        log.info("Comercios sin regla real: %d (cubren %d transacciones)",
                  len(counts), total_tx_sin_regla)
        log.info("Exportado: %s", comercios_file)
        log.info("Taxonomía actual: %s", taxonomia_file)
        log.info("Límite de prioridad: %s", limite_file)
        log.info("Siguiente paso: docs/GENERAR_REGLAS.md")
        return 0

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
