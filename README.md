# Finance Automation

Automated personal finance pipeline: Trade Republic → Google Sheets → Streamlit dashboard.

Exports transactions from Trade Republic via `pytr`, normalises and categorises them against a live rules engine, upserts into a Google Sheets ledger, and surfaces everything through an interactive finance dashboard.

---

## Architecture

```
Trade Republic
        |
     pytr CLI
        |
account_transactions.csv   (Spanish headers: Fecha, Tipo, Valor, Nota, ISIN, …)
        |
convert_pytr_to_clean.py
  - Maps Spanish columns → normalised schema
  - Infers type (buy / sell / card / transfer / deposit / income / dividend / interest)
  - Fetches rules live from Google Sheets "rules" tab
  - Applies rules engine → category, subcategory, rule_id, rule_confidence
  - SHA256 tx_id dedup
        |
transactions_clean_TIMESTAMP.csv   (out/, gitignored)
        |
push_to_sheets.py
  - Upserts on tx_id (updates existing rows, appends new ones)
  - Batch updates — no row-by-row API calls
  - Sorts sheet by datetime descending after every push
        |
Google Sheets — "transactions" tab
        |
derive_positions.py
  - Reads "transactions" tab directly from Google Sheets
  - Aggregates buy/sell events per ISIN → open/closed positions (epsilon logic)
  - Writes snapshot to Google Sheets "positions" tab
        |
Google Sheets — "positions" tab
        |
app/main.py  (Streamlit dashboard)
  - 5 pages: Dashboard · Gastos · Activos · Proyección · Objetivos
  - Live prices via yfinance + OpenFIGI instrument classification
  - Configurable-horizon portfolio projection (fixed or trend-based
    contributions, fixed or real-asset-history return scenarios)
  - Objetivos: solves the contribution (or contribution growth rate)
    needed to hit a target amount
```

---

## Scripts

| Script | What it does |
|---|---|
| `pipeline/convert_pytr_to_clean.py` | Normalises pytr CSV export, runs rules engine, writes `transactions_clean_*.csv` |
| `pipeline/derive_positions.py` | Reads transactions from Google Sheets, computes net positions (epsilon logic), writes `positions` tab |
| `pipeline/apply_rules_to_sheet.py` | Retroactively re-applies rules to all existing rows in the sheet (only touches category, subcategory, rule_id, rule_confidence) |
| `pipeline/run_pipeline.py` | Cross-platform Python orchestrator — runs all steps in order. `--unattended` skips the Trade Republic export (the only OTP-gated step) so the rest can run on a schedule |
| `pipeline/schedule_pipeline.py` | Installs/removes the recurring unattended run in the OS scheduler (`schtasks` on Windows, `crontab` on Unix), driven by the `schedule` block in `config.yaml` |
| `pipeline/metrics.py` | Per-run execution metrics — each step emits to a JSONL, the orchestrator consolidates one row per run into `logs/runs.csv` |
| `pipeline/run_tr_pipeline.sh` | Bash orchestrator (Unix/Mac only) |
| `sheets/push_to_sheets.py` | Upserts cleaned CSV into Google Sheets, sorts by datetime |
| `app/main.py` | Streamlit finance dashboard |
| `app/data.py` | Data loading layer — Google Sheets loaders, yfinance prices, OpenFIGI classification |

---

## Google Sheets tabs

| Tab | Purpose |
|---|---|
| `transactions` | Main ledger — all transactions from all sources |
| `rules` | Categorisation rules fetched live at every run |
| `positions` | Asset positions — written by `derive_positions.py` |
| `display_category_month` | Pivot table: category × month spend (read by dashboard) |

---

## Dashboard pages

| Page | Content |
|---|---|
| **Dashboard** | Monthly income / expenses / cashflow KPIs, spending-by-category donut and cashflow bar chart (both with a sidebar-adjustable time window — days / months), last 10 transactions |
| **Gastos** | Filterable expense analysis by year / month / category / subcategory / type; bar chart + trend line + transaction table |
| **Activos** | Open positions with live prices (yfinance), P&L vs average cost, portfolio allocation donut; bonds use nominal EUR value |
| **Proyección** | Portfolio projection over a configurable horizon (1–100 years). Contribution mode: fixed or trend-based (compounds a %/month growth on top of a base contribution). Scenario mode: fixed 5/8/12% or your real assets' value-weighted CAGR ± covariance-based volatility (manually overridable). Aportación history + trend, per-asset projection using that asset's own CAGR/volatility |
| **Objetivos** | Same inputs/scenarios as Proyección, inverted: set a target amount and horizon, and it solves the contribution needed to reach it — either a flat monthly amount, or (in variable mode) the monthly/semestral/annual growth rate a starting contribution needs to compound at |

### Price resolution

For each open position the dashboard:
1. Calls **OpenFIGI** (`/v3/mapping` by ISIN) to classify the instrument as `bond`, `etf`, `equity`, `crypto`, or `unknown` (cached 24 h).
2. **Bonds** (`XS*` ISINs or OpenFIGI class `bond`) — skip yfinance, use quantity directly as nominal EUR value.
3. **Crypto** (`XF000*` ISINs or OpenFIGI class `crypto`) — derive ticker from asset name via `yf.Search("{name} EUR")` → `*-EUR` pair.
4. **ETF / equity / unknown** — search yfinance by ISIN, then by the broker's name, then by OpenFIGI's own official ticker/name (a broker label like "Physical Gold USD (Acc)" often doesn't match Yahoo's naming, but OpenFIGI's record usually does).
5. If no price is found for a non-bond position, the card shows `—` and the position is excluded from portfolio totals and the allocation donut.
6. LSE-listed ETFs quoted in GBp (pence) are automatically converted to GBP before EUR conversion.

---

## Rules engine

Rules are stored in the `rules` tab of the Google Sheets spreadsheet and fetched live before every run — no code change needed to add or modify rules.

Schema: `rule_id, enabled, priority, direction, applies_to_type, match_field, match_type, match_value, category, subcategory`

| Field | Values |
|---|---|
| `match_field` | `merchant_norm` or `type` |
| `match_type` | `contains`, `equals`, `exists`, `regex` |
| `direction` | `in`, `out`, or empty (matches both) |
| `applies_to_type` | `card`, `transfer`, `buy`, etc., or empty (matches all types) |

First matching rule (by priority ascending) wins. Unmatched rows get `rule_confidence = 0`.

Writing rules: see [docs/REGLAS.md](docs/REGLAS.md) — every column explained,
when to use each match type, and copy-paste recipes.

Starting from scratch with your own transactions, the shipped example rules
cover almost nothing. See
[docs/GENERAR_REGLAS.md](docs/GENERAR_REGLAS.md) for a workflow that
generates a batch of candidate rules with an LLM from your own merchants,
which you review before applying.

---

## Position logic

`derive_positions.py` accumulates signed share counts from the `rule_notes` JSON field of each `event_domain = asset` transaction:

| pytr_type | Effect |
|---|---|
| `Compra` | Add shares |
| `Venta` | Subtract shares |
| `Dividendo` | Ignored (cash event, no share change) |

Epsilon thresholds avoid phantom open positions from fractional rounding:

| ISIN prefix | Epsilon |
|---|---|
| `XS*` (bonds) | Exact comparison — nominal is always integer |
| `XF000*` (crypto) | `1e-6` |
| Everything else (equities, ETFs) | `0.1` |

---

## Type inference

`convert_pytr_to_clean.py` maps pytr's Spanish `Tipo` values using a decision tree:

| Condition | type |
|---|---|
| `Valor < 0` and ISIN present | `buy` |
| `Valor < 0` and Nota starts with "Transacción con tarjeta" | `card` |
| `Valor < 0` otherwise | `transfer` |
| `Tipo == Venta` | `sell` |
| `Tipo == Dividendo` | `dividend` |
| `Tipo == Intereses` | `interest` |
| `Nota == Einzahlung` or contains own name | `deposit` |
| everything else (`Valor >= 0`) | `income` |

---

## Output schema

Each cleaned transaction has 25 columns:

`tx_id, source, source_file, import_batch_id, date, datetime, amount, currency, merchant_raw, merchant_norm, description, category, subcategory, rule_id, rule_confidence, type, account, status, notes, created_at, raw_json, rule_notes, event_domain, tipus, year_month`

---

## Setup

En Windows basta con ejecutar `setup.bat`: valida la ubicación del repositorio,
exige Python 3.11+, crea `.venv`, instala las dependencias, verifica que todo
importa y prepara `config.yaml`. Es idempotente — se puede relanzar sin miedo.

```
setup.bat           # instala desde requirements.txt
setup.bat lock      # versiones exactas de requirements.lock (necesita Python 3.12+)
```

Con `config.yaml` y las credenciales ya puestas, `scripts\init_sheet.bat` crea
las pestañas y cabeceras de tu Google Sheet. Es idempotente y nunca sobrescribe
celdas con contenido; `scripts\init_sheet.bat --dry-run` enseña lo que haría.
También deja trece reglas en la pestaña `rules`, pero solo si está vacía: las
`tr-` categorizan los movimientos de inversión por tipo y valen para cualquier
usuario, y las `ejemplo-` enseñan el formato para que copies. Acaban en un
cajón de sastre que manda a `Otros` lo que ninguna reclame. Se desactivan
borrando la fila o poniendo `enabled` a `FALSE`. El
esquema de las pestañas vive en `schema.py`, que es la fuente única para el
dashboard, el pipeline y ese script.

### Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

Cubren la capa de carga de datos frente a hojas vacías, incompletas o mal
configuradas. Son offline: sustituyen Google Sheets por un doble de prueba, así
que no hacen falta credenciales ni red.

Instalación manual (Linux/Mac o a mano):

```bash
python -m venv .venv

# Unix/Mac
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt    # pipeline + dashboard
```

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your own `google_sheets.spreadsheet_id` and (optionally)
`pipeline.account_holder_name`. `config.yaml` is gitignored — it's per-user,
never committed.

`account_holder_name` matters if you track **more than one account** (e.g. a
bank account and Trade Republic): a transfer whose note contains that name is
treated as `deposit` (money moving between your own accounts) instead of
`income`, so it doesn't inflate the dashboard's income/cashflow figures. If
you only track **one** account and use it purely to move money in from
outside (e.g. Trade Republic as your only source, topping it up to invest),
leave `account_holder_name` **blank** — otherwise every top-up looks like
your own name sending you money, gets classified as a self-transfer, and
disappears from "Ingresos" even though it's genuinely new money entering the
system. The dashboard shows a tooltip (ⓘ) on the affected KPI/chart whenever
this field is set, as a reminder.

Place `credentials/gdrive-sa.json` (Google Service Account with Sheets editor access) before running.

First time setting this up from scratch (new Google Sheet, new service account,
new Trade Republic login)? See [SETUP.md](SETUP.md) for the full
step-by-step.

---

## Run

```bash
# Full pipeline: export from TR → clean → derive positions → push to Sheets
# Windows (cross-platform)
python pipeline/run_pipeline.py

# Unattended run — steps 2-4 only, no OTP needed. This is what the scheduler calls.
python pipeline/run_pipeline.py --unattended

# Unix/Mac
bash pipeline/run_tr_pipeline.sh

# Individual pipeline steps
python pipeline/convert_pytr_to_clean.py
python pipeline/derive_positions.py
python sheets/push_to_sheets.py                          # uses newest CSV in out/
python sheets/push_to_sheets.py out/transactions_clean_TIMESTAMP.csv

# Dry run (preview without writing)
python sheets/push_to_sheets.py --dry-run

# Retroactively re-apply rules to all existing sheet rows
python pipeline/apply_rules_to_sheet.py
python pipeline/apply_rules_to_sheet.py --dry-run

# Delete a batch from the sheet
python sheets/push_to_sheets.py --delete-batch 20260519T132412Z

# Launch dashboard
streamlit run app/main.py
```

Windows convenience scripts — double-click, or run from a terminal:

| Script | Equivalent to |
|---|---|
| `setup.bat` | environment setup (venv, dependencies, checks) — the only one at the root, since it is the entry point |
| `scripts/init_sheet.bat` | `sheets/init_sheet.py` — creates the tabs, headers and default rules |
| `scripts/run_full_pipeline.bat` | `pipeline/run_pipeline.py` — full pipeline, Trade Republic login included |
| `scripts/run_pipeline_unattended.bat` | `pipeline/run_pipeline.py --unattended` — steps 2–4, no OTP. What the scheduled task runs |
| `scripts/apply_rules.bat` | `pipeline/apply_rules_to_sheet.py` — re-applies the rules to every row already in the sheet. Shows the diff first and asks before writing |
| `scripts/exportar_comercios_sin_regla.bat` | `pipeline/exportar_comercios_sin_regla.py` — exports the merchants no real rule covers yet, for [docs/GENERAR_REGLAS.md](docs/GENERAR_REGLAS.md). Read-only |
| `scripts/importar_reglas.bat` | `pipeline/importar_reglas_csv.py` — appends new rules from a CSV to the `rules` tab and re-sorts by priority. Never overwrites an existing `rule_id`. Shows the diff first and asks before writing |
| `scripts/seleccionar_modelos.bat` | `pipeline/seleccionar_modelos.py` — decides which forecasting models to use, from your own data. Skips the work when no new month has arrived |
| `scripts/schedule_pipeline.bat` | `pipeline/schedule_pipeline.py` — scheduled execution. Defaults to `--status` |
| `scripts/launch_dashboard.bat` | `streamlit run app/main.py` |

On a fresh install run `setup.bat`, `init_sheet.bat`, `run_full_pipeline.bat`
and `launch_dashboard.bat`, in that order. Each script `cd`s to the project
root first, so it works both double-clicked and called from a terminal.

Only `run_full_pipeline.bat` needs the Trade Republic login. The other two
pipeline entry points work offline against what is already stored, which is
what makes them useful while testing rules.

---

## Scheduled execution

The pipeline splits into two planes. Step 1 (`pytr export_transactions`)
requires an OTP sent to your phone on every authentication, so it cannot run
unattended — that's a Trade Republic constraint, not a design choice. Steps 2–4
(clean, push, derive positions) need no interaction and can run on a schedule:
they re-apply the current rules to the ledger and refresh the positions
snapshot.

Configure the cadence in `config.yaml`:

```yaml
schedule:
  enabled: true
  frequency: daily          # hourly | daily | weekly
  time: "07:00"             # HH:MM local, ignored when frequency=hourly
  day_of_week: monday       # only when frequency=weekly
  task_name: FinanceAutomationPipeline
```

```bash
python pipeline/schedule_pipeline.py --install       # register the task
python pipeline/schedule_pipeline.py --status        # show what's registered
python pipeline/schedule_pipeline.py --remove        # unregister
python pipeline/schedule_pipeline.py --install --dry-run
```

On Windows, `scripts/schedule_pipeline.bat` wraps the same commands and
defaults to `--status` when run with no arguments.

Windows registers a Programador de tareas entry via `schtasks` (may need an
elevated console); Unix writes a marked entry into the user's `crontab`.

## Run monitoring

Every run appends one row to `logs/runs.csv`: `run_id`, start and end
timestamps, total duration, mode (`full` / `unattended`), status, the step that
failed if any, per-step durations, and the row counts each step reported —
source rows, rows written, rules matched/unmatched, rows updated and inserted
in the sheet, sheet total, and open/closed/anomalous positions.

Mechanically, each step process emits its numbers as a JSON line into
`logs/metrics_<run_id>.jsonl`; the orchestrator flattens them into a single
`runs.csv` row and deletes the intermediate file. Per-run text logs stay in
`logs/pipeline_<run_id>.log`, with the last 10 kept.

---

## Credentials

`credentials/gdrive-sa.json` — Google Service Account key file. Gitignored. Must have editor access to the target spreadsheet.

---

## What's gitignored

- `credentials/` — service account key
- `config.yaml` — per-user config (spreadsheet ID, account holder name); `config.example.yaml` is the tracked template
- `out/` — generated CSVs
- `data/`, `logs/` — generated data and per-run logs
- `account_transactions.csv` — pytr export
- `all_events.json` — raw pytr account-events export
- `.venv/`

---

## History

The project's first version ran on n8n over an AWS Lightsail instance; it was migrated to the pure-Python pipeline described in this README for the reasons covered in the project's write-up (version control, debuggability, rule-engine complexity). No workflow exports or infrastructure notes from that version are kept in this repository.
