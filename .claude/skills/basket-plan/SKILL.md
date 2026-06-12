---
name: basket-plan
description: >-
  Runbook for changing the Fidelity growth portfolio through baskets via the two
  sanctioned methods. Use this whenever the user wants to rebalance, re-weight,
  recompose, resize, trim, add to, or scale a basket/sleeve (e.g. "add MSFT and IGV
  to the AI Platform basket", "trim the semis sleeve by $5k", "resize the whole
  growth basket to $40k", "bring the off-band sleeve back into its policy band", or
  asks for a basket order plan). Enforces Method A (recompose) vs Method B (resize),
  keeps out-of-basket tickers (FXAIX, SPAXX) out of basket math, and emits a
  brokerage-ready order-plan markdown. Decision-support only; nothing auto-executes.
---

# Basket plan (Method A / Method B)

The portfolio is changed **only** through baskets, and there are **exactly two**
sanctioned ways to do it. Anything else (folding individual funds into a basket,
silently moving a ticker between sleeves) is out of bounds. Full governance is in
[AGENTS.md](../../../AGENTS.md) section 1.

A basket is a named group of holdings sourced from the Fidelity `Basket Name` column
(`Position.basket_name`). Policy bands (min/max portfolio weight) and thesis come
from `config/growth_universe.yaml`, matched to baskets by holdings overlap.

## Environment (Windows / PowerShell)

```powershell
& .\.venv\Scripts\python.exe main.py basket-plan [options]
```

ASCII-only output. The order plan is written under `output/reports/<date>/`.

## Step 1 — see the current decomposition first

Omit `--basket` to print every basket, its components, dollar values, and policy-band
status. Always look here before proposing a change, so you know the current total and
whether the sleeve is in band.

```powershell
& .\.venv\Scripts\python.exe main.py basket-plan
# against a specific snapshot: --snapshot <path>   (default: latest)
```

## Step 2 — choose the method

Pick exactly one method per change. They compose (you may recompose, then resize),
but each is a distinct command.

**Method A — recompose (intra-basket).** Change the component ratios/percentages
*inside* a basket. The basket's total dollar value is held fixed unless you pass an
explicit new total. Use this to swap or re-weight names within a sleeve.

```powershell
& .\.venv\Scripts\python.exe main.py basket-plan --basket "<name>" --recompose "MSFT=10,IGV=10,GOOG=25"
# keep total fixed (default) OR override it:
& .\.venv\Scripts\python.exe main.py basket-plan --basket "<name>" --recompose "..." --new-total <$>
```

**Method B — resize (whole-basket).** Add or remove dollars to/from the *entire*
basket, preserving the (possibly just-recomposed) component ratios. Use this to scale
a sleeve up or down as a unit.

```powershell
& .\.venv\Scripts\python.exe main.py basket-plan --basket "<name>" --resize-to <$>     # scale to a target total
& .\.venv\Scripts\python.exe main.py basket-plan --basket "<name>" --resize-by <+/-$>  # add (+) or remove (-) dollars
```

Useful extras: `--universe <path>` (policy bands, default `config/growth_universe.yaml`),
`--min-trade-dollars <$>` (suppress dust trades), `--output-dir <dir>`.

### Which method? Quick decision

- "Change what's *inside* this sleeve / swap names / re-weight components" -> **A**.
- "Make this whole sleeve bigger or smaller, keep the mix" -> **B**.
- "Re-weight the names AND change the sleeve size" -> **A then B** (two commands).

## Step 3 — out-of-basket tickers are edited directly

Individual funds and out-of-basket tickers (e.g. `FXAIX`, `SPAXX`) are **not** part of
any basket. Never fold them into `--recompose` or a resize. They are edited directly
and documented separately in the "out-of-basket" section of the order plan. If the
user lumps one into a basket request, split it out and flag it.

## Step 4 — review the order plan against the bands

The command emits a brokerage-ready markdown order plan (`write_basket_order_plan`) —
buy/sell rows with dollar and share deltas — saved under `output/reports/<date>/`.
Before handing it over:

- Respect each sleeve's policy band; **flag and justify** any basket left ABOVE or
  BELOW band rather than silently leaving it.
- Honor the hard constraints in `config/persistent_context.yaml` (position caps,
  80/10/10 long/options/cash target).
- Surface any basket/sleeve mismatch; never silently reassign a ticker's sleeve.

## Step 5 — execution is manual

The plan is decision-support. The human places the trades in Fidelity, then logs them
and records the decision so the next snapshot reconciles:

```powershell
& .\.venv\Scripts\python.exe main.py log-trade -t TICK -a BUY|SELL -s <shares> -p <price> -r "<why>"
& .\.venv\Scripts\python.exe main.py record-decision --date <d> --summary "<what was decided>"
```

## Guardrails

- Only Method A or Method B — no other path changes basket exposure.
- Out-of-basket tickers never enter a basket method.
- No order routing, no auto-execution; the order plan is a recommendation.
- For sleeve-level target weights from an optimizer (ERC / max-sharpe) rather than a
  hand-specified recompose, use `rebalance-weights` instead — it proposes target
  weights, which you then translate into Method A/B basket moves.
