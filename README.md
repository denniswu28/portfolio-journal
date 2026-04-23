# Portfolio Tracker

A command-line portfolio tracking and LLM prompt generation tool for Fidelity portfolios imported from exported Fidelity positions CSV files.

## Features

- **Fidelity CSV Import** — load positions directly from a Fidelity positions export, including cash rows and fractional shares
- **Live Quotes** — optionally refresh prices via yfinance
- **Analytics** — cost basis, unrealized/realized P&L, Sharpe ratio, max drawdown, win rate
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

The Fidelity loader maps columns such as `Symbol`, `Description`, `Quantity`, `Last Price`, `Current Value`, `Today's Gain/Loss`, `Cost Basis Total`, and `Average Cost Basis`. It also preserves fractional shares, recognizes money-market cash rows such as `SPAXX**`, and tolerates Fidelity's trailing empty fields and footer text.

### 3. View portfolio status

```bash
python main.py status
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
│   │   └── market_data.py          # Live quotes via yfinance
│   │
│   ├── portfolio/
│   │   ├── tracker.py              # Build enriched PortfolioSnapshot
│   │   └── analytics.py            # Cost basis, P&L, performance metrics
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
│   ├── journal.json                # Daily journal with snapshots, trades, prompts, decisions
│   ├── trade_history.json          # Trade log
│   └── rationale_log.json          # Trade rationale archive
│
├── output/
│   └── prompts/                    # Auto-saved prompt files
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

### CLI Reference

```bash
python main.py sync -i FILE              # Load from a Fidelity positions CSV
python main.py sync -i FILE --refresh-prices
                                        # Load from Fidelity CSV and then fetch live quotes

python main.py status                    # Current portfolio table
python main.py analytics                 # Performance metrics

python main.py log-trade -t TICKER -a BUY -s SHARES -p PRICE -r "Rationale"
python main.py history                   # Recent trades
python main.py history --ticker AAPL     # Filter by ticker

python main.py prompt                    # Trade recommendation prompt
python main.py prompt --type review      # Portfolio review prompt
python main.py prompt --type risk        # Risk check prompt
python main.py prompt -q "Your question" # Custom question
python main.py prompt -o my_prompt.txt   # Save to specific file

python main.py record-decision --response-file llm.txt
                                        # Save the LLM response into the daily journal
python main.py journal                   # Show the latest journal entry
python main.py journal --date 2026-04-22
                                        # Show a specific day
```

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
