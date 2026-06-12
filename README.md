# Portfolio Tracker

A command-line portfolio tracking and LLM prompt generation tool for Fidelity portfolios imported from exported Fidelity positions CSV files.

## Features

- **Fidelity CSV Import** — load positions directly from a Fidelity positions export, including cash rows and fractional shares
- **Fidelity Daily Bundles** — group each day's positions, allocation, geography, style, and periodic-return CSVs under a dated folder
- **Live Quotes** — optionally refresh prices via yfinance
- **Analytics** — cost basis, unrealized/realized P&L, Sharpe ratio, max drawdown, win rate
- **Reports & Plots** — export metrics spreadsheets plus return/drawdown, P&L, concentration, allocation, geography, and style plots
- **Portfolio Theory Rebalance** — research broad secular-growth sleeves, fetch Yahoo Finance history, and export equal-vol, ERC, Sharpe-weighted, and max-Sharpe target weights
- **Basket Engine** — first-class baskets from the Fidelity `Basket Name` column, changed only via Method A (recompose) or Method B (resize), with policy-band checks and brokerage-ready order plans
- **Options Harness** — deterministic QuantLib pricing/greeks/payoff, a Level-2 strategy validator, an option screener (which put to sell / call to buy), portfolio greeks + stress, and a semi-automated daily monitor
- **Trade Log** — record trades with free-text rationale and tags
- **Daily Journal** — keep one daily record of snapshots, P&L, prompts, and LLM decisions
- **LLM Prompt Engine** — generate ready-to-paste prompts for ChatGPT, Claude, etc.
- **CLI** — all operations via a clean `python main.py` interface

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Sync your Fidelity portfolio

Download your Positions CSV from Fidelity, then run:

```bash
python main.py sync --input-file Portfolio_Positions_Apr-22-2026.csv
```

The Fidelity loader maps columns such as `Symbol`, `Description`, `Quantity`, `Last Price`, `Current Value`, `Today's Gain/Loss`, `Cost Basis Total`, and `Average Cost Basis`. It also preserves fractional shares, recognizes money-market cash rows such as `SPAXX**`, uses Fidelity's `Date downloaded ... ET` footer for historical snapshot timestamps, and tolerates trailing empty fields and footer text.

To keep the full daily Fidelity export set together, organize the files into a dated bundle and sync from that folder:

```bash
python main.py organize-exports --date 2026-05-06 --move --include-snapshots \
  data/portfolio_snapshots/Portfolio_Positions_May-06-2026.csv \
  data/portfolio_snapshots/Asset_allocation.csv \
  data/portfolio_snapshots/Geographic_exposure.csv \
  data/portfolio_snapshots/Periodic_returns.csv \
  data/portfolio_snapshots/style.csv

python main.py sync-bundle --date 2026-05-06
```

The bundle folder uses normalized names such as `positions.csv`, `asset_allocation.csv`, `geographic_exposure.csv`, `periodic_returns.csv`, and `style.csv`, plus a `manifest.json` that records the original filenames and detected dates.

Daily workflow after downloading Fidelity CSVs:

```bash
python main.py organize-exports --date YYYY-MM-DD --move FILES...
python main.py sync-bundle --date YYYY-MM-DD
python main.py report --output-dir output/reports
python main.py prompt --type trade -q "Analyze tomorrow's trade plan."
```

### 3. View portfolio status

```bash
python main.py status
python main.py analytics
python main.py report --output-dir output/reports
python main.py rebalance-weights --method erc --period 3y --interval 1wk
```

### 4. Log a trade

```bash
python main.py log-trade -t NVDA -a BUY -s 5 -p 920.00 \
  -r "Adding on pullback while keeping semiconductor exposure within limits" \
  --tags "semis,fidelity"
```

### 5. Generate an LLM prompt

```bash
# Trade recommendations prompt (default)
python main.py prompt --question "Should I trim my semiconductor exposure?"

# Portfolio review prompt
python main.py prompt --type review

# Risk check prompt
python main.py prompt --type risk --question "Am I violating any of my Fidelity portfolio rules?"
```

The generated prompt is printed to the terminal and auto-saved to `output/prompts/`.

### 6. Record the LLM response in your journal

```bash
python main.py record-decision \
  --prompt-file output/prompts/tomorrow_trade_prompt.txt \
  --summary "Trim GLDM and keep tech exposure steady" \
  --response-file my_llm_response.txt
```

### 7. Review the daily journal

```bash
python main.py journal
python main.py journal --date 2026-04-22
```

---

## Repository Structure

```
portfolio-journal/
├── requirements.txt
├── main.py                         # CLI entry point
│
├── config/
│   ├── settings.yaml               # Application settings
│   └── persistent_context.yaml     # Your investment strategy & constraints
│
├── src/
│   ├── data_ingestion/
│   │   ├── models.py               # Pydantic data models
│   │   ├── paste_parser.py         # Legacy text parser, not part of the Fidelity workflow
│   │   ├── csv_loader.py           # Load Fidelity positions CSV exports
│   │   ├── fidelity_bundle.py       # Organize daily Fidelity CSV bundles
│   │   ├── fidelity_analysis_loader.py
│   │   │                            # Load allocation, geography, style, returns CSVs
│   │   └── market_data.py          # Live quotes via yfinance
│   │
│   ├── portfolio/
│   │   ├── tracker.py              # Build enriched PortfolioSnapshot
│   │   ├── analytics.py            # Cost basis, P&L, performance metrics
│   │   ├── reporting.py            # Spreadsheet and return-series exports
│   │   └── plots.py                # Static PNG report charts
│   │
│   ├── trade_log/
│   │   ├── logger.py               # Record trades to JSON
│   │   └── history.py              # Query/filter trade history
│   │
│   ├── prompt_engine/
│   │   ├── prompt_builder.py       # Jinja2 templates + context assembly
│   │   └── formatter.py            # Token estimation + truncation
│   │
│   └── utils/
│       └── config_loader.py        # YAML config helpers
│
├── data/
│   ├── portfolio_snapshots/        # Dated Fidelity bundles and saved JSON snapshots
│   │   └── YYYY-MM-DD/
│   │       ├── positions.csv
│   │       ├── asset_allocation.csv
│   │       ├── geographic_exposure.csv
│   │       ├── periodic_returns.csv
│   │       ├── style.csv
│   │       ├── manifest.json
│   │       └── snapshot_YYYYMMDD_HHMMSS.json
│   ├── journal.json                # Daily journal with snapshots, trades, prompts, decisions
│   ├── trade_history.json          # Trade log
│   └── rationale_log.json          # Trade rationale archive
│
├── output/
│   ├── prompts/                    # Auto-saved prompt files
│   └── reports/                    # CSV spreadsheets and PNG report plots
│
└── tests/                          # pytest test suite
```

---

## Configuration

Edit `config/persistent_context.yaml` to customize your strategy:

```yaml
investment_strategy: "Growth-oriented..."
risk_tolerance: "Moderate-Aggressive"
investment_horizon: "6 months"
constraints:
  - "No single position > 10% of portfolio"
  - "Keep 5-15% cash reserve"
rules:
  - "Cut losses at -20%"
  - "Take profits when gain exceeds 50%"
```

Edit `config/settings.yaml` to adjust snapshot, trade history, journal, prompt output, cache TTL, and token limits.

---

## Fidelity Workflow

This README documents the Fidelity CSV workflow only.

### Export from Fidelity

Download the Positions CSV from Fidelity and point `sync --input-file` at that file.

If you also download Fidelity's supplemental analysis exports, use `organize-exports` to keep the daily set together under `data/portfolio_snapshots/YYYY-MM-DD/` and then run `sync-bundle --date YYYY-MM-DD`.

Supported daily bundle files:

- `positions.csv` — authoritative holdings snapshot source
- `asset_allocation.csv` — asset class breakdown by security
- `geographic_exposure.csv` — region/country exposure by security
- `style.csv` — style-box exposure by security
- `periodic_returns.csv` — Fidelity-reported time-weighted and money-weighted account returns

`periodic_returns.csv` is report/graph context only. It can add source-labeled Fidelity return points to `output/reports/return_series.csv`, but it does not create synthetic holdings snapshots.

### Dated Bundle Layout

Daily exports live under `data/portfolio_snapshots/YYYY-MM-DD/`:

```text
data/portfolio_snapshots/2026-05-06/
├── positions.csv
├── asset_allocation.csv
├── geographic_exposure.csv
├── periodic_returns.csv
├── style.csv
├── manifest.json
└── snapshot_20260506_140500.json
```

`positions.csv` is the authoritative holdings input. The supplemental files enrich analysis only:

- `asset_allocation.csv` adds domestic stock, foreign stock, bonds, short-term, and other exposure.
- `geographic_exposure.csv` adds region and country exposure.
- `style.csv` adds style-box exposure such as Large Blend, Large Growth, and Medium Growth.
- `periodic_returns.csv` adds Fidelity-reported time-weighted and money-weighted return points.

Files that do not contain their own date, such as allocation, geography, and style, use the folder date as their `as_of` date. `periodic_returns.csv` uses the period-end date inside the first row, for example `Prior month end performance as of Apr-30-2026`.

### Backfilling History

There are two backfill paths:

- If a historical `positions.csv` exists, run `sync-bundle --date YYYY-MM-DD`. The snapshot timestamp comes from Fidelity's `Date downloaded ... ET` footer, so the generated JSON lands on the correct historical date.
- If only `periodic_returns.csv` exists, the app can add source-labeled Fidelity return rows to `return_series.csv`. These rows enrich the return graph, but they are not treated as holdings snapshots and are not used for position-level P&L or weights.

Repeated intraday snapshots are preserved on disk. Analytics and reports use the latest snapshot per calendar day for return metrics, so multiple same-day syncs do not distort daily return calculations.

### Report Outputs

Run:

```bash
python main.py report --output-dir output/reports
```

Core outputs:

- `metrics_summary.csv` - headline performance metrics such as cumulative return, annualized return, volatility, Sharpe, Calmar, and max drawdown.
- `portfolio_timeseries.csv` - snapshot-derived daily value, return, running peak, and drawdown rows.
- `return_series.csv` - source-labeled return rows from snapshots and Fidelity periodic returns.
- `return_drawdown.png` - cumulative return and drawdown chart.
- `unrealized_pnl.png` - latest unrealized P&L by position.
- `position_weights.png` - latest position concentration chart.

Supplemental outputs appear when the dated bundle has the relevant Fidelity CSVs:

- `asset_allocation_summary.csv` and `asset_allocation.png`
- `geographic_exposure_summary.csv` and `geographic_exposure.png`
- `style_summary.csv` and `style_exposure.png`
- `fidelity_periodic_returns.csv`

### Portfolio Theory Rebalance

Run:

```bash
python main.py rebalance-weights --method erc --period 3y --interval 1wk
```

The command reads `config/growth_universe.yaml`, pulls Yahoo Finance price history for each sleeve proxy, computes historical returns, volatility, covariance, and Sharpe ratios, then exports:

- `rebalance_weights_YYYYMMDD_HHMMSS.csv` - spreadsheet target weights, current weights, target dollars, trade dollars, and method comparisons.
- `rebalance_plan_YYYYMMDD_HHMMSS.md` - human-readable rebalance plan with sleeve roles, warnings, and method notes.

Supported methods:

- `equal-vol` - inverse-volatility weights.
- `erc` - equal-risk-contribution/risk-parity approximation.
- `sharpe-weighted` - inverse-volatility weights tilted toward positive individual Sharpe ratios.
- `max-sharpe` - bounded long-only tangency allocation from historical excess returns.

The default universe is Boist-derived rather than broadly secular. It keeps memory/storage, CPU/foundry/packaging, semiconductor beta, and AI platforms as the core growth engine, then adds smaller connected sleeves for data-center power/grid, electrical and thermal equipment, data-center connectivity/facilities, power generation/uranium, industrial metals/materials, agentic cybersecurity, industrial automation/edge AI, and defense/aerospace. Broad US, ex-US, gold/metals, and Treasury/TIPS sleeves remain ballast. Edit `config/growth_universe.yaml` to change proxies, candidate holdings, sleeve minimums/maximums, cash target, or trade-size threshold.

### Journal And Prompt Enrichment

When a dated bundle has supplemental Fidelity exports, `sync-bundle` records exposure context in the daily journal. The journal can show top asset classes, regions, countries, styles, and Fidelity-reported TWR values:

```bash
python main.py journal --date 2026-05-06
```

Generated trade, review, and risk prompts include the same exposure context when it is available for the selected snapshot.

### CLI Reference

```bash
python main.py sync -i FILE              # Load from a Fidelity positions CSV
python main.py sync -i FILE --refresh-prices
                                        # Load from Fidelity CSV and then fetch live quotes
python main.py organize-exports --date YYYY-MM-DD FILES...
                                        # Group Fidelity CSV exports into a dated folder
python main.py sync-bundle --date YYYY-MM-DD
                                        # Load positions.csv and attach supplemental CSV context

python main.py status                    # Current portfolio table
python main.py analytics                 # Performance metrics
python main.py report                    # Metrics CSVs and PNG plots (incl. basket summary)

python main.py basket-plan               # Show the basket decomposition vs policy bands
python main.py basket-plan --basket "AI Platform" --recompose "MSFT=7,IGV=7,GOOG=25"
                                        # Method A: change component percentages (total fixed)
python main.py basket-plan --basket "AI Memory and Storage" --resize-by -300
                                        # Method B: add/remove $ from the whole basket

python main.py options-chain -t SMH      # Near-the-money chain with IV
python main.py options-analyze -u SMH --structure bull-put-spread --strikes 580,530 --expiry 2026-07-17
                                        # Price a Level-2 structure: net debit/credit, max P/L, greeks
python main.py options-screen -u SNDK --direction income
                                        # Rank which puts to sell (POP, RoR, EV)
python main.py log-option -u SMH --structure bull-put-spread --strikes 580,530 \
  --expiry 2026-07-17 --net-debit -553 -r "boist put-sell"
                                        # Record an opened option position
python main.py monitor                   # Re-mark options, evaluate TP/SL/roll/assignment/event rules

python main.py log-trade -t TICKER -a BUY -s SHARES -p PRICE -r "Rationale"
python main.py history                   # Recent trades
python main.py history --ticker AAPL     # Filter by ticker

python main.py prompt                    # Trade recommendation prompt
python main.py prompt --type review      # Portfolio review prompt
python main.py prompt --type risk        # Risk check prompt
python main.py prompt --type options     # Options strategy prompt (basket + greeks context)
python main.py prompt -q "Your question" # Custom question
python main.py prompt -o my_prompt.txt   # Save to specific file

python main.py record-decision --response-file llm.txt
                                        # Save the LLM response into the daily journal
python main.py journal                   # Show the latest journal entry
python main.py journal --date 2026-04-22
                                        # Show a specific day
```

---

### Baskets and Options Harness

The portfolio is changed **only through baskets**, in two ways (see [AGENTS.md](AGENTS.md)):

- **Method A — recompose**: change component percentages inside a basket; the basket
  dollar total stays fixed unless you pass `--new-total`.
- **Method B — resize**: add or remove dollars to/from the whole basket with
  `--resize-to`/`--resize-by`, preserving component ratios.

Baskets come from the Fidelity `Basket Name` column; policy bands (min/max weight) come
from `config/growth_universe.yaml`, matched by holdings overlap. Individual out-of-basket
tickers (e.g. `FXAIX`, `SPAXX`) are edited directly and reported separately. Each plan is
written to `output/reports/<date>/basket_plan_*.md`.

The **options harness** is deterministic-first (QuantLib + scipy): pricing, greeks,
payoff, probability of profit, and risk never come from an LLM. Account privilege is
**Level 2 + margin** — buy-writes, covered calls (+roll), long calls/puts, cash-secured
puts, long straddles/strangles, spreads ≤ 4 legs, and covered puts. `validate_level2()`
rejects naked calls and any undefined-risk short leg. Every order ticket specifies the
underlying, right, strikes, expiry/DTE, structure, action per leg, net debit/credit,
contracts ("hands"), max loss/profit, breakevens, margin/assignment, and exit rules.

- `options-analyze` writes a full order ticket plus a payoff plot.
- `options-screen` ranks defined-risk candidates (which put to sell / call to buy) by
  probability of profit, annualized return on margin, and expected value.
- `log-option` records an opened position to `data/options_positions.json`.
- `monitor` re-marks open positions, evaluates take-profit / stop-loss / time-stop /
  assignment / event rules (events from `config/event_calendar.yaml`), and writes alerts
  plus recommended orders to `output/reports/<date>/monitor_*.md`. It never executes.

---

## Fidelity CSV Notes

The intended input is the downloaded Fidelity positions export. The CSV loader reads the Fidelity headers directly, derives cash from money-market rows, aggregates duplicate symbols across baskets, and keeps Fidelity-specific quirks such as fractional shares and trailing empty columns from breaking the import.

---

## Journal Workflow

The app maintains a daily journal in `data/journal.json`.

- `sync` records the latest portfolio snapshot and daily P&L summary
- `log-trade` appends executed trades to the same day entry
- `prompt` auto-logs the generated prompt metadata and output path
- `record-decision` stores the LLM's actual response after you paste or save it

This keeps your portfolio state, executed actions, generated prompts, and LLM recommendations tied to the same trading day.

---

## Running Tests

```bash
pytest tests/ -v
```
