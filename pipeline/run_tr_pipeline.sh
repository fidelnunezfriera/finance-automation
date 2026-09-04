#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJ_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

cd "$PROJ_DIR"

echo "[1/5] Activating virtualenv"
source .venv/bin/activate

# Read values from config.yaml via Python (pyyaml is in the venv)
SOURCE_FILE="$(python -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['pipeline']['source_file'])")"
OUTPUT_DIR="$(python  -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['pipeline']['output_dir'])")"
LOG_DIR="$(python     -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['logging']['log_dir'])")"

# Establish run ID and shared log file (Python scripts inherit this env var)
PIPELINE_RUN_ID="$(date -u +%Y%m%dT%H%M%S)"
export PIPELINE_RUN_ID

mkdir -p "$PROJ_DIR/$LOG_DIR"
LOG_FILE="$PROJ_DIR/$LOG_DIR/pipeline_${PIPELINE_RUN_ID}.log"

# Append a timestamped line to console + log file
log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%S)] [INFO ] [run_tr_pipeline.sh] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

T_START=$(date +%s)
log "Pipeline started  run_id=$PIPELINE_RUN_ID"

log "[2/5] Exporting transactions from Trade Republic"
pytr export_transactions

if [ ! -f "$SOURCE_FILE" ]; then
  log "ERROR: $SOURCE_FILE not generated"
  exit 1
fi

log "[3/5] Cleaning transactions"
python pipeline/convert_pytr_to_clean.py

NEWEST_LEDGER="$(ls -1t "$OUTPUT_DIR"/transactions_clean_*.csv 2>/dev/null | head -n 1)"
if [ -z "$NEWEST_LEDGER" ]; then
  log "ERROR: no transactions_clean_*.csv found in $OUTPUT_DIR/"
  exit 1
fi
log "Ledger: $NEWEST_LEDGER"

log "[4/5] Pushing transactions to Google Sheets"
python sheets/push_to_sheets.py "$NEWEST_LEDGER"

log "[5/5] Deriving positions"
python pipeline/derive_positions.py

if [ ! -f "out/derived_positions_latest.csv" ]; then
  log "ERROR: derived_positions_latest.csv not generated"
  exit 1
fi

T_END=$(date +%s)
log "Pipeline completed in $((T_END - T_START))s  run_id=$PIPELINE_RUN_ID"
