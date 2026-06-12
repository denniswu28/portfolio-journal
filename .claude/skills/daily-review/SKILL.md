---
name: daily-review
description: >-
  Runbook for the portfolio-journal daily review of the real Fidelity growth
  portfolio. Use this whenever the user asks to "run the daily review", "do the
  daily advisory", "what should I do today/tomorrow", "review my portfolio",
  "build today's report", or hands over a fresh Fidelity CSV export to ingest and
  turn into the day's dated/gated action packet. Covers both the read-only
  `daily-advisory` orchestrator and the full ingest -> report -> rebalance ->
  monitor -> prompt -> record pipeline. Decision-support only: it surfaces alerts
  and recommended orders, it never routes or auto-executes anything.
---

# Daily review

Produce the day's dated, gated, prioritized advisory for the Fidelity growth
portfolio. The numbers come from the deterministic harness; the LLM only adds a
narrative overlay. Nothing here places orders — a human executes in Fidelity and
logs the result.

Governance is canonical in [AGENTS.md](../../../AGENTS.md). This skill is the
operational recipe; defer to AGENTS.md when a rule question comes up.

## Environment (Windows / PowerShell)

Always use the project venv — bare `python` may resolve to global.

```powershell
& .\.venv\Scripts\python.exe main.py <command> [options]
```

Console/file output is ASCII-only. Artifacts land under `output/reports/<date>/`.

## Pick the mode

**Mode A — read-only advisory (most days).** A snapshot already exists and you just
want today's prioritized packet. One command does the whole read-only pass
(metrics, signals, rule alerts, options gate, and an LLM prompt):

```powershell
& .\.venv\Scripts\python.exe main.py daily-advisory --date <YYYY-MM-DD>
# add --include-option-screens [--screen-underlying TICK,TICK] for live option ideas
# add --no-network to skip all yfinance calls (offline / rate-limited)
```

It writes `daily_advisory_<date>.md` + `.json` to `output/reports/<date>/` and prints
the portfolio value, cash %, the **OPTIONS GATE** state, and any ACTION-level rule
breaches. Read the markdown to the user and lead with the gate state and the action
items.

**Mode B — fresh Fidelity export (ingest day).** New CSV(s) need to become a
snapshot before anything else is valid. Run the pipeline in order:

```powershell
# 1. Group the day's Fidelity CSVs into the dated bundle folder
& .\.venv\Scripts\python.exe main.py organize-exports --date <d> --move <FILES...>

# 2. Build + save the snapshot (parses baskets from the Basket Name column)
& .\.venv\Scripts\python.exe main.py sync-bundle --date <d> [--refresh-prices] [--cash <$>]

# 3. Metrics, basket summary, plots
& .\.venv\Scripts\python.exe main.py report --output-dir output/reports/<d>

# 4. Now run Mode A on top
& .\.venv\Scripts\python.exe main.py daily-advisory --date <d> --include-option-screens
```

Use `sync` (single `--input-file`) instead of `sync-bundle` only for a one-off CSV
that is not part of a dated bundle.

## Step 0 (optional) - refresh the daily catalyst brief (news bridge)

The daily news/catalyst input is human-curated through an external LLM, then ingested as
a structured file - nothing scrapes the web (AGENTS.md S3). Catalysts are context only;
they never move deterministic numbers.

```powershell
# 1. Emit a structured research prompt grounded in today's holdings + watchlist + events
& .\.venv\Scripts\python.exe main.py catalyst-prompt --date <YYYY-MM-DD>

# 2. Paste output/prompts/catalyst_research_<date>.txt into Perplexity / Claude / ChatGPT
#    (live web), copy the YAML answer back.

# 3. Validate + store it as data/catalysts/catalyst-<date>.yaml
& .\.venv\Scripts\python.exe main.py catalyst-ingest --date <date> --file <pasted.txt>
```

`daily-advisory` then picks up the latest catalyst file automatically and adds a
"Daily catalysts" section. Use `--no-catalysts` to skip, or `--catalyst-file` to override.

## When the review surfaces work

The advisory flags issues; acting on them routes through the dedicated workflows —
do not improvise portfolio or option changes here:

- **A basket/sleeve is off-band, or the user wants to re-weight or resize a sleeve**
  -> use the [basket-plan](../basket-plan/SKILL.md) skill (Method A / Method B).
  Never fold out-of-basket tickers (FXAIX, SPAXX) into a basket method.
- **An option idea, screen, or open-position alert needs sizing/validation** -> use
  the [options-analysis](../options-analysis/SKILL.md) skill. Respect the gate label.
- **Deeper monitoring of open option positions / baskets** between full reviews:

  ```powershell
  & .\.venv\Scripts\python.exe main.py monitor --output-dir output/reports/<d>
  ```

## The options gate (read this before presenting any option as actionable)

`daily-advisory` and `monitor` print an **OPTIONS GATE** line. An option idea is only
**EXECUTABLE** when BOTH hold (see [src/advisory/gating.py](../../../src/advisory/gating.py)):

1. The account has the Level-2 privilege (`options_enabled`, or the account is in
   `enabled_accounts`), and
2. account/position value >= the `$10k` minimum (hard rule, even for an enabled account).

Until both clear, every option idea is labeled **"advisory only — not executable"**.
Never present a gated option ticket as something the user can place today.

## LLM overlay

`daily-advisory --with-prompt` (default) writes a prompt to `output/prompts/`. For a
standalone prompt:

```powershell
& .\.venv\Scripts\python.exe main.py prompt --type trade|review|risk|options -q "<question>"
```

The LLM is advisory only — it never invents prices, greeks, POP, or P&L. Those come
from the harness. News/events enter only via `config/event_calendar.yaml` and the
dated thesis markdown `data/boist-YYYY-MM-DD.md`; nothing scrapes the web to trade.

## Close the loop

After the human acts in Fidelity, persist what actually happened so the next review
is grounded:

```powershell
& .\.venv\Scripts\python.exe main.py log-trade  -t TICK -a BUY|SELL -s <shares> -p <price> -r "<why>"
& .\.venv\Scripts\python.exe main.py log-option -u TICK --structure <name> --strikes <...> --expiry <d> --net-debit <$> ...
& .\.venv\Scripts\python.exe main.py record-decision --date <d> --summary "<what was decided>"
```

## Guardrails

- Decision-support only — no order routing, no broker API, no auto-execution.
- Deterministic numbers only; the LLM never fabricates option/price/greek figures.
- Honor the hard constraints in `config/persistent_context.yaml` (position caps,
  80/10/10 long/options/cash target, take-profit > +50%, stop-loss at -20%).
- Surface basket/sleeve band breaches — never silently reassign a ticker's sleeve.
