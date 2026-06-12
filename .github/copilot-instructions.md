# GitHub Copilot instructions — Portfolio Journal

Authoritative principles live in [AGENTS.md](../AGENTS.md). This file is the short,
agent-facing version. When in doubt, defer to `AGENTS.md` and
`config/persistent_context.yaml`.

## What this project is
A CLI portfolio-tracking + LLM-prompt tool for a Fidelity growth portfolio, extended
with a deterministic options/risk harness (QuantLib + scipy) and a semi-automated
daily monitor. It is decision-support only — it never routes live orders.

## Non-negotiable rules
1. **Change the portfolio only through baskets**, via two methods:
   - **Method A — recompose**: change component percentages inside a basket
     (total fixed unless overridden).
   - **Method B — resize**: add/remove dollars to/from the whole basket, preserving
     ratios.
   Individual / out-of-basket tickers (e.g. `FXAIX`, `SPAXX`) are edited directly and
   reported separately. Baskets come from the Fidelity `Basket Name` column; policy
   bands come from `config/growth_universe.yaml`.
2. **Options are Level 2 + margin**: buy-writes, covered calls (+roll), long
   calls/puts, cash-secured puts, long straddles/strangles, spreads ≤ 4 legs, covered
   puts. **No naked calls / undefined-risk short legs** — `validate_level2()` enforces
   this.
3. **Every option order is fully specified**: underlying, right, strike(s), expiry/DTE,
   structure, action+direction per leg, net debit/credit, contracts ("hands"), max
   loss/profit, breakevens, margin & assignment, and exit rules (TP / SL / time stop).
   An order missing any field is not executable.
4. **Deterministic-first**: prices, greeks, payoff, POP, and risk come from
   `src/options/` (QuantLib/scipy) — never from an LLM. LLM output is advisory overlay.
5. **No auto-execution.** `monitor` writes alerts/recommended orders; a human places
   and logs trades. News/events enter only via `config/event_calendar.yaml` and the
   dated thesis markdown.

## How to work in this repo
- Use the venv: `& .\.venv\Scripts\python.exe ...` (Python 3.14; bare `python` may be
  global). Tests: `& .\.venv\Scripts\python.exe -m pytest -q`.
- CLI commands are Click in `main.py`; mirror existing patterns and write artifacts to
  `output/reports/<date>/`.
- Add/adjust tests under `tests/` for any change; mock yfinance, keep pure math
  testable.
- Respect `config/persistent_context.yaml` hard constraints (position caps, 80/10/10
  long/options/cash, take-profit > +50%, stop-loss at −20%).
- Keep changes minimal and idiomatic; do not introduce live trading, secrets, or
  broker APIs.

## Key modules
- `src/portfolio/baskets.py` — basket model + Method A/B + order-plan markdown.
- `src/options/` — pricing, strategies (Level-2 validator), screener, risk, monitor.
- `src/data_ingestion/market_data.py` — quotes, option chains, risk-free rate, realized
  vol (yfinance; greeks are computed in `src/options/pricing.py`, not fetched).
- `main.py` — `basket-plan`, `options-chain`, `options-analyze`, `options-screen`,
  `log-option`, `monitor`, plus the existing sync/report/prompt/journal commands.
