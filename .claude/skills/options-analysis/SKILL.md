---
name: options-analysis
description: >-
  Runbook for analyzing, screening, and validating defined-risk Level-2 options on
  the Fidelity growth portfolio using the deterministic QuantLib/scipy harness. Use
  this whenever the user wants to price or analyze an option structure, pull an option
  chain, screen for income (sell puts) or bullish (buy calls) ideas, size a covered
  call / cash-secured put / vertical spread / straddle / strangle, check max
  loss/profit/breakevens/POP/greeks, or log an option trade. Enforces the Level-2
  defined-risk rules (no naked/undefined-risk shorts), the 12-field fully-specified
  order, and the options gate. Deterministic numbers only; advisory, never executable
  while gated, and nothing auto-executes.
---

# Options analysis (Level-2, defined-risk)

Every price, greek, payoff, probability-of-profit, and P&L number comes from the
deterministic harness in `src/options/` (QuantLib BSM + scipy). The LLM never invents
option numbers. Full governance is in [AGENTS.md](../../../AGENTS.md) section 2.

## Environment (Windows / PowerShell)

```powershell
& .\.venv\Scripts\python.exe main.py <options-command> [options]
```

ASCII-only output. Tickets/plots/payoffs are written under `output/reports/<date>/`.

## What is allowed (Level-2 + margin)

Permitted structures: buy-writes, covered calls (and rolls), long calls/puts,
cash-secured puts, long straddles/strangles, spreads up to 4 legs, covered puts
(short-stock secured).

**Forbidden:** naked/uncovered calls and any short leg whose risk is not defined by a
long leg or by held stock/cash. `validate_level2()` in
[src/options/strategies.py](../../../src/options/strategies.py) rejects these — if a
request implies an undefined-risk short, stop and reshape it into a defined-risk
structure (add the protective leg, or secure with cash/stock) before pricing.

## Workflow

### 1. Look at the chain (optional context)

```powershell
& .\.venv\Scripts\python.exe main.py options-chain -t TICK [--expiry YYYY-MM-DD] [--width 8]
# default expiry: nearest ~45 DTE
```

### 2a. Analyze a specific structure

Use this when the user already knows the structure they want priced and risk-checked.

```powershell
& .\.venv\Scripts\python.exe main.py options-analyze -u TICK --structure bull-put-spread --strikes "95,90" --expiry YYYY-MM-DD --contracts 2
# OR build leg-by-leg (repeatable --leg "ACTION RIGHT STRIKE [CONTRACTS]"):
& .\.venv\Scripts\python.exe main.py options-analyze -u TICK --leg "sell put 95" --leg "buy put 90"
# secure short legs: --secured cash|stock|short_stock ; covered call backing: --shares <n>
# overrides: --vol <dec> --rate <dec> --spot <$> --american/--european
```

This validates Level-2, prices the structure, and writes an order ticket + payoff plot
(max loss/profit, breakevens, greeks, POP).

### 2b. Screen for candidates

Use this when the user has a thesis but not a specific strike set.

```powershell
& .\.venv\Scripts\python.exe main.py options-screen -u TICK --direction income|bullish [--width <$>] [--top 5]
# income  -> sell cash-secured puts (omit --width) or put spreads (with --width)
# bullish -> buy long calls (omit --width) or call spreads (with --width)
```

### 3. Require a fully-specified order (all 12 fields)

An option idea is **not executable** unless every field is present. If any is missing,
say so and fill it before presenting the order:

1. Underlying ticker
2. Right (CALL/PUT) per leg
3. Strike(s)
4. Expiry (date) and DTE
5. Structure / strategy name
6. Action (open/close/roll) and direction (buy/sell) per leg
7. Net debit or credit limit
8. Number of contracts ("hands")
9. Max loss and max profit ($)
10. Breakeven(s)
11. Margin / buying-power effect and assignment risk
12. Exit rules: take-profit, stop-loss, and a time stop (DTE to close by)

### 4. Apply the gate before calling anything executable

An option idea is only **EXECUTABLE** when BOTH hold (see
[src/advisory/gating.py](../../../src/advisory/gating.py)):

1. Account has the Level-2 privilege (`options_enabled` or in `enabled_accounts`), and
2. account/position value >= the `$10k` minimum (hard rule).

Until both clear, label the idea **"advisory only — not executable"**. Never present a
gated ticket as something the user can place today. `monitor` and `daily-advisory`
print the live gate state.

## Default exit discipline (unless a plan states otherwise)

- Debit structures: take profit near **+50%** of value; cut at **-50%** of entry
  debit; close by **14-21 DTE** rather than holding to expiry.
- Premium-selling (cash-secured puts): manage at ~**50%** of max profit; defend/roll
  on a short-strike delta or price breach; reduce size into known events.

## Monitor and log

```powershell
# re-mark open option positions + baskets, evaluate exit/roll/assignment/event rules:
& .\.venv\Scripts\python.exe main.py monitor --output-dir output/reports/<d>

# after the human places the trade in Fidelity, log it:
& .\.venv\Scripts\python.exe main.py log-option -u TICK --structure <name> --strikes "<...>" --expiry <d> \
    --contracts <n> --net-debit <+debit/-credit> [--secured cash|stock|short_stock] \
    [--take-profit 0.5] [--stop-loss 0.5] [--close-by-dte 21] -r "<why>"
```

## Backtest a structure idea (optional)

```powershell
& .\.venv\Scripts\python.exe main.py options-backtest -u TICK --structure cash-secured-put --dte 30 --otm 0.05 ...
```

## Guardrails

- Defined-risk only; `validate_level2()` rejects naked/undefined-risk shorts.
- Deterministic numbers only — never let the LLM invent prices, greeks, POP, or P&L.
- No order routing, no auto-execution; tickets are recommendations.
- News/events enter only via `config/event_calendar.yaml` and the dated thesis
  markdown — nothing scrapes the web to trade.
