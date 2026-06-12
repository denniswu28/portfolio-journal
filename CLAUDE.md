# CLAUDE.md — Portfolio Journal

Deterministic-first CLI for managing a **real** Fidelity growth/options portfolio.
It ingests Fidelity CSV exports, tracks baskets, runs a QuantLib/scipy options harness
and a quant suite (technicals, backtests, factor models), and generates LLM prompts.
**It is decision-support only — it never routes orders and nothing auto-executes.**

[AGENTS.md](AGENTS.md) is the canonical governance document. The load-bearing rules,
restated so they're always in context:

- **Portfolio changes only via baskets** — Method A (recompose, intra-basket) or
  Method B (resize, whole-basket). Out-of-basket tickers (FXAIX, SPAXX) are edited
  individually, never folded into a basket method.
- **Options must be fully-specified, defined-risk, Level-2.** `validate_level2()`
  rejects naked/undefined-risk shorts. Permitted: covered calls, cash-secured puts,
  long options, spreads ≤ 4 legs, straddles/strangles.
- **Deterministic numbers only.** All prices, greeks, payoff, POP, P&L, metrics, and
  backtests come from the harness (QuantLib + scipy + pandas). The LLM is advisory and
  never invents numbers.
- **News/events enter only through structured inputs** — `config/event_calendar.yaml`
  and the dated thesis markdown `data/boist-*.md`. Nothing scrapes the web to trade.
- **Options gating (planned):** the main cash account's Level-2 privilege is PENDING
  and an option order needs the position/account ≥ $10k. Option ideas are gated/labeled
  "advisory only — not executable" until that clears (see [ROADMAP.md](ROADMAP.md)).

## Environment & commands (Windows / PowerShell)

Use the project venv at `.venv` (Python 3.14). Never use bare `python` (may resolve to
global). Verify heavy libs (QuantLib) import in `.venv` before relying on them.

```powershell
# Run the CLI
& .\.venv\Scripts\python.exe main.py <command> [options]
# Run tests
& .\.venv\Scripts\python.exe -m pytest -q
```

## Architecture (module map)

- `src/data_ingestion/` — Fidelity CSV/paste parsing, yfinance market data
  (`get_price_history`, `get_ohlc_history`, `get_option_chain`, `realized_volatility`,
  `get_risk_free_rate`), pydantic models (`PortfolioSnapshot`, `Position`, `Trade`).
- `src/portfolio/` — `tracker` (snapshots), `analytics` (metrics; **daily/252 only**),
  `baskets` (Method A/B), `optimizer` (ERC/max-sharpe rebalance, `estimate_returns`,
  `risk_contribution_pct`), `reporting`, `plots`.
- `src/options/` — `pricing` (QuantLib BSM, greeks, IV), `strategies` (builders,
  `validate_level2`, `analyze_strategy`, `mark_strategy`), `screener`, `monitor`,
  `risk`, `events`, `reporting`.
- `src/quant/` — **quant suite** (period-aware): `technicals` (indicators), `signals`
  (`SignalSet`), `models` (CAGR/Sharpe/Sortino/MDD metrics + result dataclasses),
  `backtest` (lookahead-safe engine + `walk_forward`), `strategies_quant`
  (equal-weight, momentum, optimizer-backed sleeve), `optimize` (grid + walk-forward,
  anti-overfit IS-vs-OOS), `factor` (OLS factor/risk model), `options_backtest`
  (synthetic-chain), `reporting`.
- `src/prompt_engine/` — Jinja2 templates + token budgeting (advisory only).
- `src/trade_log/` — trade/option/journal JSON stores.

Snapshots live in `data/portfolio_snapshots/<date>/`; artifacts in
`output/reports/<date>/`. Thesis files: `data/boist-YYYY-MM-DD.md`.

## CLI commands

Ingest/track: `organize-exports`, `sync`, `sync-bundle`, `status`, `analytics`,
`report`, `journal`. Rebalance/baskets: `rebalance-weights`, `basket-plan`.
Options: `options-chain`, `options-analyze`, `options-screen`, `monitor`, `log-option`.
Trades/LLM: `log-trade`, `history`, `prompt`, `record-decision`.
**Quant suite:** `signals`, `backtest` (`--walk-forward`), `optimize-params`,
`factor-report`, `options-backtest`.

## Conventions

- Click commands in `main.py` (`@cli.command`), `tabulate`/`click.echo` output, write
  artifacts under `output/reports/<date>/`. Pin deps in `requirements.txt`.
- **Every new module gets a `tests/` test; mock network (yfinance) in tests** — pass
  injected `price_history`/`prices`, or `monkeypatch` the fetch symbol in the module
  under test. Keep pure math testable.
- **ASCII-only console/file output** — no box-drawing glyphs (Windows-hostile).
- Quant metrics are **period-aware** (`periods_per_year`); do not reuse
  `analytics.compute_metrics` (252-only) for weekly/monthly backtests.
- Backtests must stay **lookahead-safe**: strategies see only `history <= date`; weights
  apply to the next bar (`weights.shift(1) * returns`).

## What NOT to do

No secrets, no live order routing, no broker API, no auto-execution, no LLM-invented
option/price/greek numbers, no folding out-of-basket tickers into a basket method, no
presenting an option ticket as executable while gated.
