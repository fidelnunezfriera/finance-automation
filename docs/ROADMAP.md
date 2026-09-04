# Finance Automation — Roadmap

## ✅ Core Pipeline (completed)
- [x] pytr CSV extraction (manual trigger due to TR OTP authentication)
- [x] Spanish column mapping and type inference decision tree
- [x] SHA256 dedup by tx_id
- [x] Rules engine — fetched live from Google Sheets `rules` tab
- [x] Support for `merchant_norm`, `type`, and `description` as match fields
- [x] Full field mapping to Google Sheets schema (25 columns)
- [x] `push_to_sheets.py` — upsert by tx_id, sorted by datetime descending
- [x] `apply_rules_to_sheet.py` — retroactive rule application to full historical data
- [x] `config.yaml` — all hardcoded values externalized
- [x] Structured logging — per-run log files in `logs/`, last 10 kept automatically
- [x] `derive_positions.py` — reads transactions from Google Sheets, computes open/closed positions with epsilon logic, writes `positions` tab
- [x] Repo cleanup — dead files removed, duplicate code fixed
- [x] Full test run — 4555 rows across multiple sources, 4555/4555 categorized correctly
- [x] Excel serial date conversion for manually-pasted rows in the dashboard data layer
- [x] `run_pipeline.py` / `run_tr_pipeline.sh` step order fixed — push to Sheets now runs before deriving positions, so positions reflect the transactions just imported instead of the previous run's snapshot
- [x] `monthly_investments` nets buys minus sells — rotating between assets no longer inflates the contribution history
- [x] `google_sheets.spreadsheet_id` and `pipeline.account_holder_name` externalized to `config.yaml` (gitignored, `config.example.yaml` tracked as template) — no personal data hardcoded in source
- [x] `SETUP.md` — step-by-step setup guide for a new user/machine (own Sheet, own service account, own TR login)
- [x] `--unattended` mode — splits the OTP-gated Trade Republic export from steps 2–4, which run with no interaction
- [x] `schedule` block in `config.yaml` + `schedule_pipeline.py` — configurable execution cadence registered in the OS scheduler (`schtasks` / `crontab`)
- [x] `metrics.py` — per-run execution metrics consolidated into `logs/runs.csv` (durations per step, row counts, status)

## ✅ Visualization Layer (completed)
- [x] Streamlit dashboard (`app/main.py`) with dark fintech theme
- [x] Dashboard page — monthly KPIs, spending-by-category donut and cashflow bar chart with sidebar-adjustable time windows (days / months)
- [x] Gastos page — filterable expense analysis, category breakdown, monthly trend
- [x] Activos page — open positions with live yfinance prices, P&L vs avg cost, portfolio allocation donut
- [x] Proyección page — configurable horizon (1–100y), fixed or trend-based contribution mode, fixed or real-asset-history (value-weighted CAGR ± covariance-based volatility) scenarios, aportación history + trend, per-asset projection using that asset's own CAGR/volatility
- [x] Objetivos page — set a target amount instead of a contribution; solves the fixed contribution needed (or, in variable mode, the monthly/semestral/annual contribution growth rate needed) to reach it
- [x] OpenFIGI instrument classification (bond / etf / equity / crypto / unknown) — cached 24 h
- [x] Bond positions use quantity as nominal EUR value; non-bond failed lookups show `—` and are excluded from totals
- [x] GBp (British pence) normalisation for LSE-listed ETFs
- [x] Automatic price resolution — no hardcoded ISIN map; yfinance search cascades ISIN → broker name → OpenFIGI's official ticker/name

## 🤖 AI Layer
- [ ] RAG chatbot — natural language queries over personal finance data (e.g. "how much did I spend on restaurants in March?")
- [ ] Vector DB integration for CSV/transaction data

## ☁️ Cloud Migration
- [ ] Migrate pipeline to AWS Lambda
- [ ] Store data in S3
- [ ] Mobile trigger — run full pipeline from phone with a single button
- [ ] Evaluate cost of always-on vs on-demand execution

## 📚 Documentation
- [x] Architecture diagrams — see [arquitectura.md](arquitectura.md)
- [x] Setup guide for a new machine — see [SETUP.md](../SETUP.md)
- [ ] Comparison with alternatives (n8n, Zapier, cloud-native ETL tools)
