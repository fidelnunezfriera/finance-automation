"""
Recogida de métricas de ejecución del pipeline.

Cada paso del pipeline corre en su propio proceso, así que no puede devolver
valores al orquestador. En vez de eso, cada script emite sus métricas como una
línea JSON en logs/metrics_<run_id>.jsonl, y run_pipeline.py las consolida al
terminar en una única fila de logs/runs.csv.

El resultado es un histórico tabular de ejecuciones — cuántas filas entraron,
cuántas se insertaron, cuánto tardó cada paso, si falló — que es lo que permite
monitorizar el pipeline sin leer logs a mano.

Uso en un script de paso:
    from pipeline.metrics import emit
    emit("clean", cfg, rows_read=len(df), rows_written=len(out))

Uso en el orquestador:
    from pipeline.metrics import consolidate
    consolidate(cfg, run_id, mode="full", status="ok", duration_s=42.1)
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent

# Orden fijo de columnas en runs.csv. Las claves que un paso no emita quedan
# vacías, de modo que el fichero se puede abrir en cualquier hoja de cálculo sin
# que las columnas bailen entre ejecuciones.
RUN_FIELDS = [
    "run_id",
    "started_at",
    "finished_at",
    "duration_s",
    "mode",
    "status",
    "failed_step",
    # duración por paso
    "export_s",
    "clean_s",
    "push_s",
    "positions_s",
    # convert_pytr_to_clean
    "source_rows",
    "rows_written",
    "rules_active",
    "rules_matched",
    "rules_unmatched",
    # push_to_sheets
    "push_rows_loaded",
    "push_rows_updated",
    "push_rows_inserted",
    "sheet_total",
    # derive_positions
    "positions_total",
    "positions_open",
    "positions_closed",
    "positions_anomalies",
]


def _run_id() -> str:
    return os.environ.get("PIPELINE_RUN_ID") or \
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _log_dir(cfg: dict) -> Path:
    d = _ROOT / cfg.get("logging", {}).get("log_dir", "logs")
    d.mkdir(exist_ok=True)
    return d


def _metrics_file(cfg: dict, run_id: str) -> Path:
    return _log_dir(cfg) / f"metrics_{run_id}.jsonl"


def emit(step: str, cfg: dict, **fields) -> None:
    """Emite las métricas de un paso. Nunca lanza: si falla, el pipeline sigue."""
    try:
        run_id = _run_id()
        record = {"step": step, "at": datetime.now(timezone.utc).isoformat(), **fields}
        with open(_metrics_file(cfg, run_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # La telemetría no debe tumbar una ejecución que por lo demás va bien.
        pass


def read_run(cfg: dict, run_id: str) -> dict:
    """Aplana todas las líneas emitidas en una ejecución a un solo diccionario."""
    path = _metrics_file(cfg, run_id)
    flat: dict = {}
    if not path.exists():
        return flat
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec.pop("step", None)
            rec.pop("at", None)
            flat.update(rec)
    return flat


def consolidate(cfg: dict, run_id: str, **run_fields) -> Path | None:
    """
    Escribe una fila en logs/runs.csv con las métricas de la ejecución completa
    y borra el .jsonl intermedio. Devuelve la ruta de runs.csv.
    """
    try:
        log_dir  = _log_dir(cfg)
        runs_csv = log_dir / "runs.csv"

        row = {k: "" for k in RUN_FIELDS}
        row["run_id"] = run_id
        row.update({k: v for k, v in read_run(cfg, run_id).items() if k in row})
        row.update({k: v for k, v in run_fields.items() if k in row})

        write_header = not runs_csv.exists()
        with open(runs_csv, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RUN_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(row)

        _metrics_file(cfg, run_id).unlink(missing_ok=True)
        _cleanup_orphans(log_dir, keep=int(cfg.get("logging", {}).get("keep_last_n_logs", 10)))
        return runs_csv
    except Exception:
        return None


def _cleanup_orphans(log_dir: Path, keep: int) -> None:
    """Purga .jsonl huérfanos de ejecuciones que abortaron sin consolidar."""
    files = sorted(log_dir.glob("metrics_*.jsonl"), key=lambda p: p.stat().st_mtime)
    for old in (files[:-keep] if keep > 0 else files):
        old.unlink(missing_ok=True)
