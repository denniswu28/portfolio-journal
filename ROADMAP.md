# Roadmap: From Manual Workflow to an Automated Quant Suite

> Educational tooling, not financial advice. Nothing here auto-executes trades.
> Governance is canonical in [AGENTS.md](AGENTS.md); this roadmap plans how the
> system grows toward an on-demand, quantitatively-grounded daily decision process.

## Progress (updated 2026-06-07)

Per the request to build the **quant foundations first**, phases were resequenced —
the analytical engine landed before the automation. Status:

- ✅ **Phase 0:** `CLAUDE.md` + Claude memory seeded; ASCII-only output; `options_gating`
  config + `OptionsGating` model + fail-closed `options_gate_status()` predicate — done.
- ✅ **Phase 2 (technicals/signals):** `src/quant/technicals.py`, `signals.py` — done.
- ✅ **Phase 3 (full quant suite):** `models.py` (period-aware metrics), `backtest.py` +
  `strategies_quant.py` (lookahead-safe engine + walk-forward), `optimize.py`
  (anti-overfit IS-vs-OOS), `factor.py` (OLS factor/risk model), `options_backtest.py`
  (synthetic-chain) — done, with CLI (`signals`, `backtest`, `optimize-params`,
  `factor-report`, `options-backtest`).
- ✅ **Phase 1 (`daily-advisory` + gating):** `src/advisory/` package (`gating`, `models`,
  `thesis`, `rules`, `reporting`, `orchestrator`) + the `daily-advisory` CLI command —
  done. Emits one dated, gated, prioritized markdown + JSON brief; option ideas are
  "label, don't hide". Verified end-to-end on the real 2026-06-05 snapshot.
- ✅ **Phase 4 (deeper integration):** `src/advisory/signal_overlay.py` enriches each
  band verdict with the technical `SignalSet` (bias + confidence: confirmed /
  counter-trend / into-strength / *-watch) — band math unchanged. Verified live: e.g.
  AI Platform TRIM + GOOG bullish -> "into-strength"; Precious Metals TRIM + GLDM bearish
  -> "confirmed". The Level-2 flip path (`options_enabled: true`) is implemented and tested.

Suite: **289 passed** (105 new tests across quant + advisory).

All planned phases are complete. Remaining optional refinements: graduate signals through
the backtester before they influence verdicts (currently raw technicals), and an optional
Windows Task Scheduler wrapper if/when an unattended cadence is wanted.

The original phase plan (with rationale) follows unchanged for reference.

---

## Context

**Why.** The portfolio is currently managed **manually**: each day/week the operator
hand-runs a chain of CLI commands, reads the latest **Boist weekly report**
(`data/boist-*.md`, external thesis by Bo Zeng), and hand-writes an
`output/reports/<date>/action_item_*.md` deciding what to buy/trim/hold, at what
price, and which options to place. The goal is to evolve this into an **on-demand,
automated, quantitatively-grounded** daily process that emits one detailed, priced,
prioritized action document — while honoring the hard governance: **deterministic
numbers only, LLM advisory only, nothing auto-executes.**

**What the repo already is.** A deterministic-first decision-support CLI (`main.py`,
~15 Click commands): Fidelity CSV bundles → snapshots → **baskets** (Method A
recompose / Method B resize) → QuantLib/scipy **options harness** (pricing, greeks,
POP, Level-2 validation, screener, monitor, risk/stress) → LLM prompts.
`backtest_baskets.py` does a single 1-month basket backtest. There is **no**
technicals/signals module, **no** orchestrator, **no** CLAUDE.md, and the Claude
memory dir is empty. Latest saved snapshot: 2026-06-05; full suite `183 passed`.

**Scope chosen.** Full phased roadmap · **on-demand** trigger (no scheduler) ·
**yfinance + computed technicals** (free, no paid API) · **full quant suite**
(walk-forward, parameter optimization, factor models, options-strategy backtests).

**Hard real-world constraint encoded everywhere.** Cannot place an option order
unless the relevant position/account ≥ **$10,000**, AND the **main cash account's
Level-2 options privilege is PENDING**. Resolution (reconciled below): option ideas
are **shown but clearly labeled "advisory only — not executable"** until both clear,
driven by config that defaults ON and can be flipped to executable later.

### Reconciliation with Codex's `daily_advisory_packet_v1_plan.md`

This roadmap merges Codex's V1 plan with the earlier plan. Adopted **from Codex**:
the dedicated **`src/advisory/` package** (models / rules / thesis / reporting — more
testable than orchestrating inside `main.py`), a typed **rules engine**, **JSON output
alongside markdown**, the command name **`daily-advisory`**, and the **Windows
box-drawing-character encoding fix**. Adopted **from the earlier plan**: **Phase 0
onboarding** (CLAUDE.md + memory), the **$10k + Level-2 gating** (your explicit
constraint — Codex omitted it for lack of that context), **on-demand only** (no
scheduler — Codex's Phase 4 Task Scheduler is deferred/optional), and the **full quant
suite**. The gate conflict is resolved as "label, don't hide" (your choice).

---

## Phase 0 — Onboarding & guardrails *(no dependencies; do first)*

1. **`CLAUDE.md`** (repo root, new) — operational, loaded every session: project
   identity; golden rules inline (basket Method A/B only; out-of-basket tickers edited
   individually; options fully-specified, defined-risk, Level-2; deterministic numbers
   only; news/events only via `config/event_calendar.yaml` + dated boist markdown;
   **options gating: main account Level-2 PENDING + $10k min → ideas shown as advisory-
   only**); venv commands (`& .\.venv\Scripts\python.exe ...`, `pytest -q`, never bare
   `python`); architecture map; daily workflow; conventions (Click + tabulate, artifacts
   to `output/reports/<date>/`, mock yfinance in tests, **ASCII-only console output —
   no box-drawing chars**); "real money, default to HOLD".

2. **Seed Claude memory** (one fact per file + `MEMORY.md` index):
   - `user-profile` — real Fidelity brokerage; growth/momentum + sector rotation,
     moderate, ~3-mo horizon, benchmark SPY; Level-2 PENDING on main account, $10k min;
     follows boist weekly thesis as overlay.
   - `project-goals` — phased roadmap → on-demand `daily-advisory` + full quant suite;
     on-demand (no scheduler); yfinance + technicals.
   - `key-constraints` — 10% single-equity cap (gold/PM exempt when documented);
     80/10/10 long/options/cash; TP +50% / SL −20%; basket Method A/B only; Level-2
     defined-risk only; deterministic-first; options gating; nothing auto-executes.
   - (Boist theses stay OUT of memory — dated rotating inputs.)

3. **Options-gating config + model + predicate** (config-driven, default-on):
   - Add `options_gating` block to `config/persistent_context.yaml`:
     `options_enabled: false`, `options_min_account_value: 10000`,
     `options_account_label`, `enabled_accounts: []`.
   - Extend `PersistentContext` in `src/data_ingestion/models.py` (line ~294) with a
     nested `OptionsGating` pydantic model (**required** — pydantic drops unknown YAML
     keys otherwise).
   - Add tested predicate `options_gate_status(ctx, account_value) -> (executable, reason)`:
     `executable=False` if `options_enabled is False` OR `account_value < min` (unless
     account in `enabled_accounts`). Drives **labeling**, not suppression (see Phase 1).
   - Tests: under-$10k → not executable, privilege-pending → not executable, YAML
     round-trip, enabled-account override.

4. **Windows encoding hygiene.** Replace box-drawing separators in existing console
   output (`status` and friends) with ASCII; add a regression test asserting CLI output
   and the advisory report contain no Windows-hostile glyphs.

---

## Phase 1 — `daily-advisory` packet *(depends on Phase 0; pure orchestration)*

The V1 milestone: one dated advisory packet produced before any manual order. New
**`src/advisory/`** package keeps logic testable; the CLI command is a thin wrapper.
**Reuses existing functions — reimplements nothing**, so numbers match standalone
commands. Read-only w.r.t. portfolio state (no trade logging, no journal schema change).

**`src/advisory/` package:**
- `models.py` — `AdvisoryRun`, `RuleAlert`, `BasketActionCandidate`,
  `OptionAdvisorySummary`, `ThesisContext` (typed results, serialize to JSON).
- `rules.py` — typed alerts from `config/persistent_context.yaml` constraints/rules:
  cash target, 80/10/10 allocation, single-position 10% cap, TP +50% flags, SL −20%
  flags, basket band drift, sleeve mismatches, out-of-basket holdings.
- `thesis.py` — load latest `data/boist-YYYY-MM-DD.md` on/before run date; extract
  title/date/path + relevant sections **without inventing trade facts** (plain text +
  light regex; no scraping, no LLM number extraction — AGENTS.md §3).
- `reporting.py` — render markdown **and** JSON.

**New CLI command** in `main.py` (mirrors existing `@cli.command` + `_load_config` +
`output/reports/<date>/` patterns):
```
daily-advisory --date <d> [--snapshot] [--thesis-file] [--universe]
               [--output-dir] [--include-option-screens] [--screen-underlying]
               [--rate] [--no-network] [--with-prompt/--no-prompt]
```
Output: `output/reports/<date>/daily_advisory_<timestamp>.md` and `.json`.

**Reused functions:** snapshot load (`tracker.py:154-172`); baskets
(`baskets.py:183,272,85`); Method A/B sizing (`baskets.py:301,377,531`); metrics
(`analytics.py:136`); sleeves (`optimizer.py:250,315`); option screen
(`screener.py:278`); monitor (`monitor.py:135,154,160`); risk (`risk.py`); events
(`events.py`); narrative (`prompt_builder.py:341`); Level-2 gate (`strategies.py:220`).
Data-prep mirrors the `monitor` command body (`main.py:1137-1203`) and `options-screen`
(`main.py:1017-1054`).

**Report sections:** 0 Header + gate banner · 1 Snapshot summary · 2 Portfolio rule
alerts (from `rules.py`) · 3 Per-position add/trim/hold verdicts with prices · 4 Basket
drift + Method A/B priced order plans + out-of-basket (FXAIX/SPAXX) + net-cash
reconciliation · 5 Thesis context (verbatim boist digest) · 6 Event-calendar timing ·
7 Open-option monitor (alerts, greeks, stress) · 8 Option candidates — **gated label**
(see below) · 9 LLM advisory prompt (paste-ready, not executed) · 10 Next CLI commands /
execution checklist.

**Gate behavior — "label, don't hide":** option ideas (screens + open-position roll
suggestions) are **computed and shown**, each tagged with `options_gate_status`. When
not executable, every ticket carries a clear banner: *"ADVISORY ONLY — not executable
until Level-2 privilege clears and position/account ≥ $10,000."* Open-position
monitoring is never gated (you must manage what's already open); when gated, any
*opening* action (e.g. rolling into a new short leg) is annotated "blocked — close-only."
A `--include-option-screens` flag controls whether new screens run at all. Flipping
`options_enabled: true` (or adding an account to `enabled_accounts`) makes the same
tickets executable with no code change.

**Graceful degradation** (per-section, never abort): missing snapshot = hard stop
("run sync first"); missing boist = proceed deterministically w/ note; `--no-network`/
yfinance down = skip live chains/marks, baskets still compute; no chain / no open
options / missing universe / rebalance failure = label that section and continue.

**Tests:** thesis loading + latest-file selection + section extraction; each rule alert
(cash, allocation, TP, SL, bands, mismatches, out-of-basket); CLI test w/ temp
snapshot/config/thesis and **all yfinance mocked**; gated vs executable labeling
(pending privilege blocks executable tag); regression that output avoids Windows-hostile
glyphs; fixture test asserting advisory sections match standalone `monitor`/`basket-plan`.

---

## Phase 2 — Technicals / signals *(depends on Phase 1 as consumer + yfinance)*

New `src/quant/technicals.py` + `src/quant/signals.py` — **hand-rolled pure pandas,
zero new deps** (avoids TA-Lib/pandas-ta build/pinning friction on Windows/Py3.14;
keeps math auditable).

- Indicators over `get_price_history` frames (`market_data.py:161`): `sma/ema`, `macd`,
  `rsi`, `roc`, `atr`, `bollinger`, `realized_vol_percentile` (reuse
  `market_data.py:343`), `relative_strength` vs SPY/sector, `ma_cross_signal`, plus
  simple support/resistance levels.
- ATR needs OHLC → add narrow `get_ohlc_history()` to `market_data.py`; close-to-close
  proxy fallback.
- Output model `SignalSet` (frozen dataclass): trend/momentum/volatility/rel_strength
  dicts + `flags` (e.g. `overbought`, `below_200dma`, `high_vol_regime`).
  `compute_universe_signals()` batches one threaded download.
- **Integration:** feed signals into the advisory's per-position verdicts (band-only →
  evidence-based) and into screener direction inference.
- New `signals` CLI command. Tests: known-answer (SMA of constant = constant, RSI
  monotonic-up → 100, ATR ≥ 0) with synthetic frames.

---

## Phase 3 — Quant suite / backtests *(depends on Phase 2 + existing optimizer)*

New `src/quant/` modules; generalize `backtest_baskets.py` into a **tested CLI module**
(Codex Phase 2) and extend to the full suite; reuse optimizer + analytics.

- **`backtest.py`** — `Strategy` Protocol; `BacktestEngine.run(...)` generalizing the
  dollar-weighted compounding kernel (`backtest_baskets.py:139`) to multi-horizon;
  **strict trailing-window slicing + next-bar fills** (lookahead guard).
  `SleeveRebalanceStrategy` wraps `build_rebalance_plan`/`estimate_returns`
  (`optimizer.py:315,276`); `SignalStrategy` tilts from `SignalSet`s. `walk_forward()`
  stitches disjoint OOS folds. Metrics via `compute_metrics` on synthetic snapshots
  (`analytics.py:136`); add only `_compute_sortino`.
- **`optimize.py`** — `grid_search` + `walk_forward_optimize` over gridable knobs
  (rebalance bands, TP/SL, MA windows). **Anti-overfit baked in:** walk-forward default;
  always report IS-vs-OOS + parameter-stability heatmap; realistic `cost_bps`; warn on
  large IS≫OOS Sharpe gap.
- **`factor.py`** — lightweight OLS factor model on `estimate_returns`; factors from free
  yfinance proxies (SPY, SMH/SOXX, GRID/PAVE, GLDM, TIP/SHY, VXUS); risk decomposition
  reuses `risk_contribution_pct` (`optimizer.py:566`).
- **`options_backtest.py`** — **no historical chains needed** (yfinance has none):
  reconstruct theoretical prices from historical spot + trailing RV (optionally IV/RV-
  calibrated once from a live chain), select strikes by delta/%-OTM, **build via existing
  builders + `validate_level2`**, entry/marks via `analyze_strategy`/`mark_strategy`
  (`strategies.py:415,373,220`) with `eval_date` advancement; exit rules from AGENTS.md §2.
  Label "theoretical, no slippage/skew."
- New CLI: `backtest`, `optimize-params`, `factor-report`, `options-backtest`
  (tabulate + md + PNG). Tests: lookahead-guard sentinel, walk-forward disjointness,
  flat-spot CSP keeps full credit / crash caps loss at width.

---

## Phase 4 — Deeper integration *(depends on 1–3)*

- Advisory consumes **backtested** signal confidences (only signals passing Phase 3
  graduate into Phase 2's live verdicts); richer event-calendar automation; options
  auto-selection tuned to backtested edge — still `validate_level2`- and gating-gated.
- **When Level-2 clears:** flip `options_enabled: true` (and/or populate
  `enabled_accounts`) — the same tickets become executable **with no code change**.
- **Optional (deferred, not default):** a Windows Task Scheduler wrapper to run the
  advisory pre-market. Off by default per the on-demand choice; add only if wanted once
  the packet is stable.

**Dependency chain:** 0 → 1 (gate/onboarding before orchestrator) → 2 (advisory consumes
signals) → 3 (backtest the signals) → 4. yfinance is cross-cutting; the `--no-network`
path keeps every phase usable offline.

---

## Key files to create / modify

| Action | Path |
|---|---|
| New | `CLAUDE.md`; memory files + `MEMORY.md` |
| Modify | `config/persistent_context.yaml` (gating block); `src/data_ingestion/models.py` (`OptionsGating`) |
| New | `src/advisory/` (`models.py`, `rules.py`, `thesis.py`, `reporting.py`) |
| Modify | `main.py` (`daily-advisory` + later `signals`/`backtest`/`optimize-params`/`factor-report`/`options-backtest`; ASCII separators) |
| Modify | `src/options/monitor.py` (gate banner); `src/data_ingestion/market_data.py` (`get_ohlc_history`) |
| New | `src/quant/` (technicals, signals, backtest, optimize, factor, options_backtest, models, reporting); `config/quant_strategies.yaml` |
| New | `tests/test_advisory_*.py`, `tests/test_options_gating.py`, `tests/test_quant_*.py` |

No new runtime dependencies (indicators hand-rolled; reuse pandas/numpy/scipy/QuantLib).

---

## Verification

Run via the venv (AGENTS.md §6): `& .\.venv\Scripts\python.exe ...`.

- **Phase 0:** `pytest -q` green incl. gating + encoding tests; load `PersistentContext`
  and assert `options_gating.options_enabled is False`; CLI output has no box glyphs.
- **Phase 1:** `daily-advisory --date <latest>` produces `.md` + `.json`; confirm gate
  banner present; option tickets tagged "ADVISORY ONLY — not executable" while gated;
  basket/monitor numbers match standalone commands; runs with `--no-network` and a
  missing boist file; rule alerts fire on a crafted snapshot.
- **Phase 2:** `signals --universe config/growth_universe.yaml` emits a report;
  known-answer tests pass; advisory verdicts cite signal flags.
- **Phase 3:** `backtest`, `optimize-params --walk-forward`, `factor-report`,
  `options-backtest` each emit md + PNG; lookahead-guard and walk-forward-disjointness
  tests pass; optimize report shows IS-vs-OOS.
- **Gate flip rehearsal:** set `options_enabled: true` with a ≥$10k account value → same
  tickets become executable; revert.
- Full `pytest -q` green after each phase; every new module has a test with yfinance mocked.
