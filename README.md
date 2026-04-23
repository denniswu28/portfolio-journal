# Paper Portfolio Tracker

A command-line portfolio tracking and LLM prompt generation tool for [Investopedia Simulator](https://www.investopedia.com/simulator/) paper trading accounts.

## Features

- **Paste Parser** — copy-paste your Investopedia portfolio page and parse it instantly (no scraping, no API keys)
- **CSV Fallback** — load positions from a simple CSV file
- **Live Quotes** — optionally refresh prices via yfinance
- **Analytics** — cost basis, unrealized/realized P&L, Sharpe ratio, alpha vs SPY, beta vs SPY, current & max drawdown, win rate
- **Benchmark Reporting** — automatically fetches SPY history and computes alpha/beta via OLS regression; alpha and beta are recorded on each snapshot as-of-date
- **Saved Reports** — `analytics` prints a performance summary and writes a markdown report to `output/reports/`
- **Trade Log** — record trades with free-text rationale and tags
- **LLM Prompt Engine** — generate ready-to-paste prompts for ChatGPT, Claude, etc.
- **CLI** — all operations via a clean `python main.py` interface

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Sync your portfolio

**Option A: Paste from Investopedia**
```bash
python main.py sync --paste
# Paste the text from your Investopedia portfolio page, then Ctrl+D
```

**Option B: Load from a text file**
```bash
python main.py sync --file --input-file my_portfolio.txt
```

**Option C: Load from CSV**
```bash
python main.py sync --csv --input-file portfolio.csv
```

CSV format:
```csv
ticker,shares,cost_basis,current_price
BABA,500,114.52,135.38
DAL,300,49.08,70.22
```

### 3. View portfolio status

```bash
python main.py status
```

### 4. Log a trade

```bash
python main.py log-trade -t AAPL -a BUY -s 50 -p 175.00 \
  -r "Breaking out above 200-day MA on strong volume" \
  --tags "momentum,tech"
```

### 5. View performance analytics

```bash
python main.py analytics
```

Fetches SPY history for the snapshot window, computes Sharpe ratio, alpha vs SPY, beta vs SPY, current and max drawdown, then prints a full report and saves it to `output/reports/report_<timestamp>.md`.

Alpha and beta are calculated via OLS regression of your portfolio's interval returns against SPY's aligned interval returns.  When fewer than 3 intervals are available the report notes "N/A (insufficient history)" rather than printing misleading values.

### 6. Generate an LLM prompt

```bash
# Trade recommendations prompt (default)
python main.py prompt --question "Should I trim my airline exposure?"

# Portfolio review prompt
python main.py prompt --type review

# Risk check prompt
python main.py prompt --type risk --question "Am I violating any of my rules?"
```

The generated prompt is printed to the terminal and auto-saved to `output/prompts/`.

---

## Repository Structure

```
paper-portfolio/
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
│   │   ├── paste_parser.py         # Parse Investopedia copy-paste text
│   │   ├── csv_loader.py           # Load from CSV
│   │   └── market_data.py          # Live quotes via yfinance
│   │
│   ├── portfolio/
│   │   ├── tracker.py              # Build enriched PortfolioSnapshot
│   │   ├── analytics.py            # Cost basis, P&L, Sharpe, alpha, beta, drawdown
│   │   └── reporting.py            # Report renderer (CLI + markdown file)
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
│   ├── portfolio_snapshots/        # Saved JSON snapshots
│   ├── trade_history.json          # Trade log
│   └── rationale_log.json          # Trade rationale archive
│
├── output/
│   ├── prompts/                    # Auto-saved prompt files
│   └── reports/                    # Auto-saved analytics reports (markdown)
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
  - "No single position > 35% of portfolio"
  - "Keep at least 5% cash reserve"
rules:
  - "Cut losses at -15%"
  - "Take profits when gain exceeds 50%"
```

Edit `config/settings.yaml` to adjust file paths, cache TTL, and token limits.

---

## CLI Reference

```bash
python main.py sync --paste              # Paste portfolio interactively
python main.py sync --file -i FILE       # Load from .txt file
python main.py sync --csv -i FILE        # Load from CSV
python main.py sync --refresh-prices     # Also fetch live quotes

python main.py status                    # Current portfolio table
python main.py analytics                 # Performance metrics + saved report
python main.py analytics -o FILE         # Save report to a custom path

python main.py log-trade -t TICKER -a BUY -s SHARES -p PRICE -r "Rationale"
python main.py history                   # Recent trades
python main.py history --ticker AAPL     # Filter by ticker

python main.py prompt                    # Trade recommendation prompt
python main.py prompt --type review      # Portfolio review prompt
python main.py prompt --type risk        # Risk check prompt
python main.py prompt -q "Your question" # Custom question
python main.py prompt -o my_prompt.txt   # Save to specific file
```

---

## How the Paste Parser Works

Copy the text from your Investopedia Simulator portfolio page. It looks like:

```
Total Value $133,169.20 Today's Change $0.00(0.00%) Total Gain/Loss $32,357.31(32.10%)
BABA Alibaba Group Holding Ltd - ADR $135.38 $0.00 (0.00%) $114.52 500 $67,690.00 $10,432.50 (18.22%) Buy More Sell
DAL Delta Air Lines, Inc. $70.22 $0.00 (0.00%) $49.08 300 $21,066.00 $6,340.71 (43.06%) Buy More Sell
```

The parser extracts:
| Field | Example |
|---|---|
| Total portfolio value | `$133,169.20` |
| Today's change | `$0.00 (0.00%)` |
| Total gain/loss | `$32,357.31 (32.10%)` |
| Per-position: ticker, company, price, cost basis, shares, market value, P&L | see above |

Cash is derived automatically: `cash = total_value - sum(market_values)`.

---

## Running Tests

```bash
pytest tests/ -v
```
