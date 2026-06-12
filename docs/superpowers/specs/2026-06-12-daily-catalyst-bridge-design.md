# Daily Catalyst Bridge — Design

> Date: 2026-06-12
> Status: Approved (design); ready for implementation planning
> Governance: subordinate to [AGENTS.md](../../../AGENTS.md). Educational tooling,
> not financial advice. Nothing here auto-executes or routes orders.

## 1. Problem & goal

The operator wants an "automated daily agent" that analyzes daily news / catalysts
alongside technicals and market data and produces an order/adjustment plan for the
real Fidelity growth portfolio.

The repo already produces the plan: `daily-advisory` emits a dated, gated, priced,
prioritized markdown + JSON packet from technicals/signals, market data, band
verdicts, rule alerts, the event calendar, the weekly Boist thesis, and an LLM prompt.
The only genuine gaps are:

1. **Live news / catalysts** — currently forbidden as a direct trade input by
   AGENTS.md §3 ("news enters only through structured inputs; nothing scrapes the web
   to trade").
2. **A repeatable daily cadence** — currently fully on-demand.

The operator's actual workflow (decided during brainstorming) is to **paste a
structured prompt into Perplexity / Claude / ChatGPT and paste the results back** —
not to run an unattended scraper, a headless model, or a cloud agent. This design
therefore extends the repo's existing "generate prompt → run externally → bring
structured results back" pattern (already used for LLM advice) to news/catalysts.

## 2. Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | News policy | Research lands in a **structured, human-curated catalyst file**; AGENTS.md §3 stays intact. |
| 2 | Runtime | **Paste-driven loop** — no scheduler, no API keys, no data leaves the machine. |
| 3 | Paste-back format | **Light structured YAML schema** (few required fields; tolerant of prose). |
| 4 | Integration depth | **Thesis-parallel context** — mirrors `thesis.py`; catalysts are display + narrative only, never alter numbers. |
| 5 | Coverage (default) | **Held positions + watchlist**, where the watchlist is the `growth_universe.yaml` sleeve holdings already returned by `known_tickers()`. Flippable. |
| 6 | Model style | **`@dataclass` + `to_dict()`** to match `src/advisory/models.py` (pydantic is only used in `data_ingestion/models.py`). Validation is explicit in the parser, not pydantic. |
| 7 | Prompt template | **Inline Jinja2 template string** (like `TRADE_INSTRUCTIONS_TEMPLATE`), not a `.j2` file. |

## 3. Data flow

```
Morning loop (paste-driven; no scheduler):

1. catalyst-prompt --date <d>
   reads latest snapshot (holdings) + growth_universe.yaml (watchlist)
   + event_calendar.yaml + latest boist thesis tickers
   -> renders a structured research prompt
   -> writes output/prompts/catalyst_research_<d>.txt and prints the path

2. [human] paste that prompt into Perplexity / Claude / ChatGPT (live web)
   -> copy the YAML answer

3. catalyst-ingest --date <d> --file pasted.txt        (or --stdin)
   parses + validates the light schema (collects warnings, fails loudly only on a
   malformed REQUIRED field) -> writes data/catalysts/catalyst-<d>.yaml + prints a
   summary (N items, M macro, warnings)

4. daily-advisory --date <d>
   NEW: loads data/catalysts/catalyst-<d>.yaml via src/advisory/catalysts.py
   -> adds a "Daily catalysts" report section
   -> shows each held ticker's catalyst beside its band verdict + signal overlay
   -> raises an event-risk note for near-term catalyst dates
   -> weaves the catalyst digest into the final LLM advisory prompt
   Band math / prices / greeks / POP / P&L are UNCHANGED.
```

The dated `data/catalysts/catalyst-<d>.yaml` is the **daily-news analog of the weekly
`data/boist-*.md` thesis**: a structured, reviewable input the operator curates.

## 4. The light schema (what the external LLM returns / what gets stored)

```yaml
as_of: 2026-06-12
generated_by: perplexity            # which tool produced this (free text)
macro:                              # market-wide catalysts (optional list)
  - summary: "May CPI cooler than expected; rate-cut odds up"
    direction: bull                 # bull | bear | neutral   (required)
    event_date: 2026-06-11          # optional
    source_url: https://...         # optional, for audit
items:                              # per-ticker catalysts (optional list)
  - ticker: NVDA                    # required
    direction: bull                 # required: bull | bear | neutral
    summary: "New hyperscaler order; analyst PT raised"   # required
    event_date: 2026-06-18          # optional (earnings, launch, decision)
    confidence: med                 # optional: low | med | high (LLM's own claim)
    source_url: https://...         # optional
    notes: "watch for profit-taking after the run"        # optional free text
freeform_notes: |                   # optional catch-all prose
  Overall risk-on tape; semis leading.
```

Required per `items` entry: `ticker`, `direction`, `summary`. Required per `macro`
entry: `direction`, `summary`. `direction`/`confidence` are validated against fixed
enums; `event_date` is parsed as ISO `YYYY-MM-DD`. Unknown keys are ignored; prose is
preserved in `notes` / `freeform_notes`.

## 5. Components (each isolated, single purpose)

### 5.1 Models — `src/advisory/models.py` (beside `ThesisContext`)
- `@dataclass CatalystItem`: `ticker: str`, `direction: str`, `summary: str`,
  `event_date: Optional[str] = None`, `confidence: str = ""`,
  `source_url: Optional[str] = None`, `notes: str = ""`, plus `to_dict()`.
- `@dataclass MacroCatalyst`: `direction: str`, `summary: str`,
  `event_date: Optional[str] = None`, `source_url: Optional[str] = None`, `to_dict()`.
- `@dataclass CatalystContext` (the loaded-into-advisory view, analog of
  `ThesisContext`): `path`, `catalyst_date`, `generated_by`, `items: List[CatalystItem]`,
  `macro: List[MacroCatalyst]`, `freeform_notes`, `near_term: List[CatalystItem]`
  (items whose `event_date` falls within the event horizon), `digest: str`,
  `stale_vs_snapshot: bool`, `found: bool`, plus `to_dict()`.
- `AdvisoryRun` gains `catalysts: CatalystContext = field(default_factory=CatalystContext)`.

### 5.2 Loader + parser — `src/advisory/catalysts.py` (mirrors `thesis.py`)
- `find_latest_catalyst_file(as_of, data_dir="data") -> Optional[Path]` — newest
  `data/catalysts/catalyst-YYYY-MM-DD.yaml` dated on/before `as_of`.
- `parse_catalyst_paste(text) -> tuple[CatalystContext, list[str]]` — parse YAML
  (tolerant: strip code fences, accept a bare items list), validate required fields +
  enums, return `(context, warnings)`. Raises `CatalystValidationError` only when there
  are **zero** valid items AND zero valid macro entries (i.e. nothing usable).
- `build_catalyst_context(as_of, data_dir="data", explicit_file=None,
  snapshot_date=None, event_horizon_days=30) -> CatalystContext` — locate + load the
  dated file, compute `near_term` and `stale_vs_snapshot`, build a short `digest`;
  returns `CatalystContext(found=False)` gracefully when no file exists.
- `CatalystValidationError(Exception)`.

### 5.3 Research-prompt template — `src/prompt_engine/catalyst_prompt.py`
- Inline Jinja2 template string (convention: like `TRADE_INSTRUCTIONS_TEMPLATE`).
- `generate_catalyst_prompt(snapshot, sleeves, events, as_of, watchlist_tickers) -> str`
  — renders: (a) explicit instructions to return ONLY the YAML schema in §4 with one
  `items` block per listed ticker; (b) the held + watchlist tickers with current
  weight/price context; (c) upcoming `event_calendar.yaml` dates inside the horizon;
  (d) macro questions (rates/FOMC, broad tape, sector rotation). No network; pure
  render from already-loaded inputs.

### 5.4 CLI commands — `main.py` (thin Click wrappers)
- `catalyst-prompt --date <d> [--universe config/growth_universe.yaml] [--snapshot]
  [--event-horizon-days 30] [--held-only] [--output-dir]` — load snapshot + sleeves +
  events, compute the watchlist via `known_tickers()` (or held-only with `--held-only`),
  call `generate_catalyst_prompt`, write `output/prompts/catalyst_research_<d>.txt`,
  echo the path + a one-line "paste into Perplexity/Claude/ChatGPT" hint.
- `catalyst-ingest --date <d> (--file PATH | --stdin) [--data-dir data]` — read the
  pasted text, `parse_catalyst_paste`, write `data/catalysts/catalyst-<d>.yaml`
  (canonicalized via the dataclass `to_dict()`), print a summary table (items, macro,
  warnings). Non-zero exit only on `CatalystValidationError`.

### 5.5 daily-advisory wiring
- `orchestrator.build_advisory_run(...)` gains a `catalyst_context: Optional[CatalystContext]`
  parameter (loaded by the CLI, mirroring how `thesis_file` is handled) and stores it on
  the `AdvisoryRun`. Pure/offline — no behavior change when `catalyst_context is None`.
- `reporting.render_markdown(...)`:
  - New section **"Daily catalysts"** (placed after the thesis section): macro list,
    per-ticker table (`ticker | direction | summary | event_date | confidence |
    source`), near-term event-risk callouts, and `freeform_notes`. ASCII-only.
  - The per-position / per-basket verdict table gains a **catalyst column** that shows
    the matching `CatalystItem.direction` (display only, beside the band verdict + the
    existing signal overlay). Empty when no catalyst for that ticker.
  - JSON output includes `catalysts` via `to_dict()`.
- `main.py daily-advisory` gains `--catalyst-file PATH` (override) and
  `--no-catalysts` (skip) flags mirroring `--thesis-file`; by default it calls
  `build_catalyst_context` for `as_of`. When `--with-prompt`, the catalyst digest is
  appended to the prompt `question` alongside the thesis digest.

### 5.6 Runbook update — `.claude/skills/daily-review/SKILL.md`
- Add a short "Step 0 — refresh catalysts (optional, the daily news bridge)" block
  documenting `catalyst-prompt -> external LLM -> catalyst-ingest -> daily-advisory`,
  and restate that catalysts are structured, human-curated context that never moves
  deterministic numbers (AGENTS.md §3 preserved).

## 6. Governance & guardrails (unchanged boundaries)

- **AGENTS.md §3 preserved.** Catalysts enter ONLY through the structured
  `data/catalysts/*.yaml`, curated by the human. No code scrapes the web; the external
  LLM is driven by a human paste, exactly like the existing `prompt` workflow.
- **Deterministic numbers untouched.** Catalysts are display + narrative only. They
  never alter band math, sizing, prices, greeks, POP, or P&L. The catalyst column sits
  beside the band verdict, like the signal overlay does today.
- **No auto-execution, no scheduler, no API keys, no broker API.** `confidence` and
  `source_url` are the external LLM's own claims, stored and labeled as such for audit.
- **ASCII-only** console/file output (Windows-hostile glyph regression covered by tests).

## 7. Error handling & degradation

- Missing catalyst file → `daily-advisory` proceeds; the section reads
  "No catalyst brief for `<date>` — run `catalyst-prompt` / `catalyst-ingest`"
  (same pattern as the thesis path). Stale file (dated before the snapshot) → "(STALE)".
- Parser: a malformed `items`/`macro` block → warning + skip; an unknown ticker (not in
  the known universe) → kept but flagged in warnings; ingest aborts only when nothing is
  usable.
- `catalyst-prompt` with no snapshot → hard error ("run sync / sync-bundle first"),
  matching `daily-advisory`.

## 8. Testing strategy (yfinance mocked; pure math; ASCII asserted)

- `tests/test_catalysts.py` — latest-file selection (on/before date), missing-file
  graceful (`found=False`), parser known-answer (well-formed paste → exact
  `CatalystContext`), prose-heavy paste → warnings but still parses, malformed-required
  → `CatalystValidationError`, enum rejection (bad `direction`), staleness flag,
  near-term horizon filter.
- `tests/test_catalyst_prompt.py` — prompt includes every held + watchlist ticker, the
  upcoming events inside the horizon, and the literal schema instructions; deterministic
  for a fixture snapshot; `--held-only` narrows the list.
- `tests/test_cli.py` (extend) — `catalyst-prompt` writes a file (temp dirs, no network);
  `catalyst-ingest` round-trips a paste → `data/catalysts/catalyst-<d>.yaml`; bad paste
  exits non-zero.
- `tests/test_advisory_*.py` (extend) — with a catalyst file present, the advisory
  markdown contains the "Daily catalysts" section + the per-ticker catalyst column;
  absent → section degrades; JSON has the `catalysts` block; ASCII-only regression.
- Full suite green: `& .\.venv\Scripts\python.exe -m pytest -q`.

## 9. Files to create / modify

| Action | Path |
|--------|------|
| New | `src/advisory/catalysts.py` |
| New | `src/prompt_engine/catalyst_prompt.py` |
| New | `data/catalysts/` (dir; gitignored like other `data/`) |
| New | `tests/test_catalysts.py`, `tests/test_catalyst_prompt.py` |
| Modify | `src/advisory/models.py` (`CatalystItem`, `MacroCatalyst`, `CatalystContext`; `AdvisoryRun.catalysts`) |
| Modify | `src/advisory/orchestrator.py` (`catalyst_context` param + store on run) |
| Modify | `src/advisory/reporting.py` ("Daily catalysts" section + catalyst column + JSON) |
| Modify | `main.py` (`catalyst-prompt`, `catalyst-ingest` commands; `--catalyst-file` / `--no-catalysts` on `daily-advisory`) |
| Modify | `.claude/skills/daily-review/SKILL.md` (Step 0 news-bridge block) |
| Modify | `tests/test_cli.py`, `tests/test_advisory_*.py` (extend) |

No new runtime dependencies (PyYAML + Jinja2 already in use).

## 10. Out of scope (deliberately)

Windows Task Scheduler, headless model invocation, cloud agents, and live web scraping
are all excluded — ruled out by the operator's paste-it-myself workflow. If an
unattended cadence is wanted later, it bolts on as a thin wrapper around
`catalyst-prompt` / `catalyst-ingest` / `daily-advisory` without touching this design.
