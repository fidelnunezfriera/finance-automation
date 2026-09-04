"""
Shared logging module for the finance automation pipeline.

Usage in any script:
    from pipeline.logger import get_logger          # if _ROOT is in sys.path
    log = get_logger(Path(__file__).stem, cfg)

Log files:
  - Named  logs/pipeline_<PIPELINE_RUN_ID>.log
  - When run_pipeline.py / run_tr_pipeline.sh sets the PIPELINE_RUN_ID env var,
    every script in the same run appends to the same file.
  - When a script runs standalone the env var is absent and a fresh timestamped
    file is created automatically.
  - Old files are pruned to keep_last_n_logs after each new file is opened.

Format:  [YYYY-MM-DDTHH:MM:SS] [LEVEL] [script_name] message
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def get_logger(script_name: str, cfg: dict) -> logging.Logger:
    lc       = cfg.get("logging", {})
    level    = getattr(logging, str(lc.get("level", "INFO")).upper(), logging.INFO)
    log_dir  = _ROOT / lc.get("log_dir", "logs")
    log_dir.mkdir(exist_ok=True)

    run_id   = os.environ.get("PIPELINE_RUN_ID") or \
               datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_file = log_dir / f"pipeline_{run_id}.log"

    logger = logging.getLogger(script_name)
    if not logger.handlers:
        logger.setLevel(level)

        fmt = logging.Formatter(
            fmt     = "[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s",
            datefmt = "%Y-%m-%dT%H:%M:%S",
        )
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

    _cleanup_logs(log_dir, keep=int(lc.get("keep_last_n_logs", 10)))
    return logger


def _cleanup_logs(log_dir: Path, keep: int) -> None:
    files = sorted(log_dir.glob("pipeline_*.log"), key=lambda p: p.stat().st_mtime)
    to_delete = files[:-keep] if keep > 0 else files
    for old in to_delete:
        old.unlink(missing_ok=True)
