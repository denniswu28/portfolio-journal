# AGENTS.md — Portfolio Journal operating principles

This file is the canonical governance document for both human and AI contributors
working in this repository. It encodes how the portfolio may be changed, how options
are specified and risk-managed, and how the deterministic harness and LLM advice
relate. Treat these as hard rules, not suggestions.

> Educational tooling, not financial advice. Nothing here auto-executes trades.

---

## 1. The portfolio is changed only through baskets — two methods

A "basket" is a named group of holdings sourced from the Fidelity export
`Basket Name` column (`Position.basket_name`). Strategic policy bands (min/max
portfolio weight) and thesis come from `config/growth_universe.yaml`, matched to each
basket by holdings overlap.

There are **exactly two** sanctioned ways to change basket exposure:

- **Method A — recompose (intra-basket).** Change the component ratios / percentages
  inside a basket. The basket's total dollar value is held fixed unless an explicit
  new total is supplied. Use this to swap or re-weight names within a sleeve
  (e.g. add MSFT/IGV into the AI Platform basket while trimming others).
  CLI: `python main.py basket-plan --basket "<name>" --recompose "TICKER=PCT,..."`.

- **Method B — resize (whole-basket).** Add or remove dollars to/from the entire
  basket, preserving the (possibly just-recomposed) component ratios. Use this to
  scale a sleeve up or down as a unit. CLI:
  `python main.py basket-plan --basket "<name>" --resize-to <$>` or `--resize-by <±$>`.

**Individual funds and out-of-basket tickers** (e.g. `FXAIX`, `SPAXX`) are not part of
any basket and are edited directly, documented separately in the "out-of-basket"
section of the order plan. Never fold them into a basket method.

Every basket change is emitted as a brokerage-ready markdown order plan
(`write_basket_order_plan`) and should be saved under `output/reports/<date>/`.

### Basket rules
- Respect the sleeve policy band; flag and justify any basket left ABOVE/BELOW band.
- Honor the hard constraints in `config/persistent_context.yaml` (position caps,
  80/10/10 long/options/cash target, take-profit > +50%, stop-loss at −20%).
- Surface basket/sleeve mismatches; never silently reassign a ticker's sleeve.

---

## 2. Options must be fully specified, defined-risk, and Level-2 compliant

Account options privilege is **Level 2 with margin**. The permitted structures are:

- Buy-writes; covered calls (and rolls); long calls/puts; cash-secured puts;
  long straddles/strangles; spreads up to 4 legs; covered puts (short-stock secured).

**Forbidden:** naked/uncovered calls and any short leg whose risk is not defined by a
long leg or by held stock/cash. The `validate_level2()` guard rejects these.

Every option order — proposed by code, an LLM, or a human — must specify **all** of:

1. Underlying ticker
2. Right (CALL / PUT) for each leg
3. Strike(s)
4. Expiry (date) and DTE
5. Structure / strategy name (e.g. bull put spread, long call, cash-secured put)
6. Action (open / close / roll) and direction (buy / sell) per leg
7. Net debit or credit limit
8. Number of contracts ("hands")
9. Max loss and max profit (in dollars)
10. Breakeven(s)
11. Margin / buying-power effect and assignment risk
12. Exit rules: take-profit, stop-loss, and a time stop (DTE to close by)

If any field is missing, the order is incomplete — do not present it as executable.

### Default exit discipline (unless a plan states otherwise)
- Debit structures: take profit near **+50%** of the structure's value; cut at
  **−50%** of the entry debit; close by **14–21 DTE** rather than holding to expiry.
- Premium-selling (cash-secured puts): manage at ~**50%** of max profit; defend or
  roll on a short-strike delta or price breach; reduce size into known events.

---

## 3. Deterministic-first; the LLM is advisory only

- All pricing, greeks, payoff, probability-of-profit, and risk numbers come from the
  deterministic harness (QuantLib + scipy in `src/options/`). Do not let an LLM invent
  option prices, greeks, or P&L.
- LLM prompts (`python main.py prompt`) are for narrative/judgment overlay on top of
  the deterministic outputs and the weekly thesis (e.g. the Bo Zeng / "boist" notes).
- News and events enter the system **only** through structured inputs
  (`config/event_calendar.yaml` and the dated thesis markdown). Nothing scrapes the
  web to trade, and nothing auto-executes.

---

## 4. Monitoring is semi-automated; execution is manual

- `python main.py monitor` re-marks open option positions and baskets, evaluates the
  exit/roll/assignment/event rules, and writes alerts + recommended orders to
  `output/reports/<date>/`. It never places orders.
- Run it on each review day. The human places any resulting trades in Fidelity and
  logs them (`log-trade`, `log-option`) and records the decision in the journal.

---

## 5. Daily workflow

```
organize-exports --date <d> --move FILES...   # group the day's Fidelity CSVs
sync-bundle --date <d>                         # build + save the snapshot (baskets parsed)
report --output-dir output/reports/<d>         # metrics, basket summary, plots
basket-plan --basket "<name>" --recompose|--resize-...   # Method A / B (as needed)
options-screen / options-analyze               # size any defined-risk options
monitor                                        # alerts, triggers, recommended orders
prompt --type trade|review|risk|options        # LLM advisory overlay
record-decision / log-trade / log-option       # persist what was actually done
```

---

## 6. Repository conventions for contributors

- **Interpreter:** use the project venv at `.venv` (Python 3.14). Run everything with
  `& .\.venv\Scripts\python.exe ...`; the bare `python` may resolve to global.
- **Tests:** `& .\.venv\Scripts\python.exe -m pytest -q`. Add tests for every new
  module under `tests/`; mock network (yfinance) and keep pure math testable.
- **CLI:** commands are Click (`@cli.command(...)`) in `main.py`; follow the existing
  option/echo/`tabulate` patterns and write artifacts under `output/reports/<date>/`.
- **Dependencies:** pin in `requirements.txt`. Heavyweight quant libs (QuantLib) are
  intentional; verify they import in `.venv` before relying on them.
- **No secrets, no live order routing, no broker API execution.** This stays a
  decision-support tool.
