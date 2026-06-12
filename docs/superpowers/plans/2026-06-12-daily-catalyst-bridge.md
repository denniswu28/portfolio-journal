# Daily Catalyst Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a paste-driven daily news/catalyst bridge: a CLI emits a structured research prompt the operator runs in an external LLM, the results are ingested into a dated, validated catalyst YAML, and `daily-advisory` folds them in as thesis-parallel context — without touching any deterministic number.

**Architecture:** New `src/advisory/catalysts.py` (loader + tolerant parser, mirroring `thesis.py`) and `src/prompt_engine/catalyst_prompt.py` (inline-Jinja2 research prompt). Two thin Click commands (`catalyst-prompt`, `catalyst-ingest`) wrap them. `daily-advisory` loads the dated `data/catalysts/catalyst-<d>.yaml` and renders a "Daily catalysts" section plus a catalyst column on the basket table; band math, prices, and greeks are unchanged.

**Tech Stack:** Python 3.14 (`.venv`), dataclasses, PyYAML, Jinja2, Click, pytest. All already in `requirements.txt` — no new deps.

**Spec:** [docs/superpowers/specs/2026-06-12-daily-catalyst-bridge-design.md](../specs/2026-06-12-daily-catalyst-bridge-design.md)

**Conventions (from AGENTS.md / CLAUDE.md):**
- Always run via the venv: `& .\.venv\Scripts\python.exe ...`. Never bare `python`.
- Tests: `& .\.venv\Scripts\python.exe -m pytest -q`. Mock network (yfinance) — these tasks need no network.
- ASCII-only console/file output. No box-drawing glyphs.
- Catalysts are display/narrative only; never alter deterministic numbers (AGENTS.md §3).

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/advisory/catalysts.py` | locate dated file, tolerant paste parser, build `CatalystContext` |
| Create | `src/prompt_engine/catalyst_prompt.py` | render the structured research prompt (inline Jinja2) |
| Create | `tests/test_catalysts.py` | parser + loader tests |
| Create | `tests/test_catalyst_prompt.py` | research-prompt tests |
| Modify | `src/advisory/models.py` | `CatalystItem`, `MacroCatalyst`, `CatalystContext`; `AdvisoryRun.catalysts` |
| Modify | `src/advisory/orchestrator.py` | accept `catalyst_context`, store on the run |
| Modify | `src/advisory/reporting.py` | "Daily catalysts" section + catalyst column on basket table |
| Modify | `main.py` | `catalyst-prompt`, `catalyst-ingest` commands; `--catalyst-file` / `--no-catalysts` flags |
| Modify | `.claude/skills/daily-review/SKILL.md` | document the news-bridge loop |
| Modify | `tests/test_cli.py`, `tests/test_advisory_reporting.py` | CLI + reporting integration tests |

---

## Task 1: Catalyst data models

**Files:**
- Modify: `src/advisory/models.py` (add after `ThesisContext`, ~line 77; add field to `AdvisoryRun`)
- Test: `tests/test_catalysts.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalysts.py`:

```python
from src.advisory.models import CatalystItem, MacroCatalyst, CatalystContext, AdvisoryRun


def test_catalyst_models_defaults_and_to_dict():
    item = CatalystItem(ticker="NVDA", direction="bull", summary="new order")
    assert item.event_date is None and item.confidence == ""
    assert item.to_dict()["ticker"] == "NVDA"

    macro = MacroCatalyst(direction="bear", summary="hot CPI")
    assert macro.to_dict()["direction"] == "bear"

    ctx = CatalystContext()
    assert ctx.found is False and ctx.items == [] and ctx.macro == []
    assert ctx.to_dict()["found"] is False


def test_advisory_run_has_catalysts_default():
    run = AdvisoryRun(
        as_of_date="2026-06-12", generated_at="t", snapshot_path=None,
        portfolio_value=0.0, cash=0.0, cash_pct=0.0, gate={},
    )
    assert run.catalysts.found is False
    assert "catalysts" in run.to_dict()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalysts.py -q`
Expected: FAIL with `ImportError: cannot import name 'CatalystItem'`.

- [ ] **Step 3: Add the models**

In `src/advisory/models.py`, after the `ThesisContext` dataclass (line ~77, before `AdvisoryRun`) add:

```python
@dataclass
class CatalystItem:
    """One per-ticker catalyst from the daily news bridge (display/narrative only)."""

    ticker: str = ""
    direction: str = "neutral"   # bull | bear | neutral
    summary: str = ""
    event_date: Optional[str] = None
    confidence: str = ""          # "" | low | med | high (the external LLM's own claim)
    source_url: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MacroCatalyst:
    """One market-wide catalyst from the daily news bridge."""

    direction: str = "neutral"
    summary: str = ""
    event_date: Optional[str] = None
    source_url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CatalystContext:
    """Loaded daily catalyst brief — the daily-news analog of ThesisContext."""

    path: Optional[str] = None
    catalyst_date: Optional[str] = None
    generated_by: str = ""
    items: List["CatalystItem"] = field(default_factory=list)
    macro: List["MacroCatalyst"] = field(default_factory=list)
    freeform_notes: str = ""
    near_term: List["CatalystItem"] = field(default_factory=list)
    digest: str = ""
    stale_vs_snapshot: bool = False
    found: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
```

Then in the `AdvisoryRun` dataclass, add this field alongside `thesis` (after line ~92):

```python
    catalysts: CatalystContext = field(default_factory=CatalystContext)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalysts.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/advisory/models.py tests/test_catalysts.py
git commit -m "feat(advisory): add catalyst data models"
```

---

## Task 2: Tolerant catalyst paste parser

**Files:**
- Create: `src/advisory/catalysts.py`
- Test: `tests/test_catalysts.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalysts.py`:

```python
import pytest
from src.advisory.catalysts import parse_catalyst_paste, CatalystValidationError

GOOD_PASTE = """```yaml
as_of: 2026-06-12
generated_by: perplexity
macro:
  - summary: May CPI cooler than expected
    direction: bull
    event_date: 2026-06-11
items:
  - ticker: nvda
    direction: bull
    summary: New hyperscaler order
    event_date: 2026-06-18
    confidence: med
    source_url: https://example.com/n
    notes: watch profit-taking
  - ticker: MU
    direction: bear
    summary: DRAM pricing softness
freeform_notes: |
  Risk-on tape.
```"""


def test_parse_good_paste():
    ctx, warnings = parse_catalyst_paste(GOOD_PASTE)
    assert warnings == []
    assert ctx.catalyst_date == "2026-06-12"
    assert ctx.generated_by == "perplexity"
    assert [i.ticker for i in ctx.items] == ["NVDA", "MU"]   # upper-cased
    assert ctx.items[0].direction == "bull"
    assert ctx.items[0].confidence == "med"
    assert len(ctx.macro) == 1 and ctx.macro[0].direction == "bull"
    assert "Risk-on" in ctx.freeform_notes


def test_parse_skips_bad_blocks_with_warnings():
    paste = """
items:
  - ticker: NVDA
    direction: sideways    # invalid enum -> skip
    summary: x
  - direction: bull        # missing ticker -> skip
    summary: y
  - ticker: AAPL
    direction: bull
    summary: good one
"""
    ctx, warnings = parse_catalyst_paste(paste)
    assert [i.ticker for i in ctx.items] == ["AAPL"]
    assert len(warnings) == 2


def test_parse_coerces_bad_optional_fields():
    paste = """
items:
  - ticker: NVDA
    direction: bull
    summary: x
    confidence: extreme        # invalid optional -> coerced to ""
    event_date: not-a-date     # invalid optional -> None
"""
    ctx, warnings = parse_catalyst_paste(paste)
    assert ctx.items[0].confidence == ""
    assert ctx.items[0].event_date is None
    assert len(warnings) == 2


def test_parse_nothing_usable_raises():
    with pytest.raises(CatalystValidationError):
        parse_catalyst_paste("items:\n  - direction: bull\n    summary: no ticker\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalysts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.advisory.catalysts'`.

- [ ] **Step 3: Write the parser**

Create `src/advisory/catalysts.py`:

```python
"""Load + parse the daily catalyst brief (the daily-news analog of thesis.py).

The brief enters ONLY through a human paste into ``catalyst-ingest`` (AGENTS.md S3):
nothing here scrapes the web. Catalysts are display/narrative context only; they never
alter deterministic numbers. Parsing is tolerant of prose and code fences but validates
the required fields and the direction/confidence enums.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from src.advisory.models import CatalystContext, CatalystItem, MacroCatalyst

_DIRECTIONS = {"bull", "bear", "neutral"}
_CONFIDENCES = {"", "low", "med", "high"}
_FILE_RE = re.compile(r"catalyst-(\d{4}-\d{2}-\d{2})\.ya?ml$", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n```\s*$", re.DOTALL)


class CatalystValidationError(Exception):
    """Raised when a paste contains no usable catalyst items or macro entries."""


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text


def _parse_iso(value, warnings: List[str], label: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        warnings.append(f"{label}: bad date '{value}' ignored")
        return None


def _build_item(raw: dict, warnings: List[str]) -> Optional[CatalystItem]:
    if not isinstance(raw, dict):
        warnings.append(f"item not a mapping, skipped: {raw!r}")
        return None
    ticker = str(raw.get("ticker", "")).strip().upper()
    direction = str(raw.get("direction", "")).strip().lower()
    summary = str(raw.get("summary", "")).strip()
    if not ticker:
        warnings.append("item missing ticker, skipped")
        return None
    if direction not in _DIRECTIONS:
        warnings.append(f"{ticker}: bad direction '{direction}', skipped")
        return None
    if not summary:
        warnings.append(f"{ticker}: missing summary, skipped")
        return None
    confidence = str(raw.get("confidence", "")).strip().lower()
    if confidence not in _CONFIDENCES:
        warnings.append(f"{ticker}: bad confidence '{confidence}' coerced to ''")
        confidence = ""
    source = raw.get("source_url")
    return CatalystItem(
        ticker=ticker, direction=direction, summary=summary,
        event_date=_parse_iso(raw.get("event_date"), warnings, ticker),
        confidence=confidence,
        source_url=str(source).strip() if source else None,
        notes=str(raw.get("notes", "")).strip(),
    )


def _build_macro(raw: dict, warnings: List[str]) -> Optional[MacroCatalyst]:
    if not isinstance(raw, dict):
        warnings.append(f"macro not a mapping, skipped: {raw!r}")
        return None
    direction = str(raw.get("direction", "")).strip().lower()
    summary = str(raw.get("summary", "")).strip()
    if direction not in _DIRECTIONS or not summary:
        warnings.append(f"macro block invalid (direction/summary), skipped: {summary[:40]!r}")
        return None
    source = raw.get("source_url")
    return MacroCatalyst(
        direction=direction, summary=summary,
        event_date=_parse_iso(raw.get("event_date"), warnings, "macro"),
        source_url=str(source).strip() if source else None,
    )


def _digest(ctx: CatalystContext) -> str:
    parts = [f"{len(ctx.items)} ticker catalysts, {len(ctx.macro)} macro"]
    for it in ctx.items[:6]:
        parts.append(f"{it.ticker} {it.direction}: {it.summary}")
    return " | ".join(parts)[:1000]


def parse_catalyst_paste(text: str) -> Tuple[CatalystContext, List[str]]:
    """Parse a pasted catalyst brief into a CatalystContext + warnings.

    Tolerant of code fences and prose; raises CatalystValidationError only when nothing
    usable (no valid items AND no valid macro) is found.
    """
    warnings: List[str] = []
    data = yaml.safe_load(_strip_fences(text)) or {}
    if isinstance(data, list):           # bare items list
        data = {"items": data}
    if not isinstance(data, dict):
        raise CatalystValidationError("Catalyst paste did not parse to a mapping.")

    items = [it for it in (_build_item(r, warnings) for r in (data.get("items") or [])) if it]
    macro = [m for m in (_build_macro(r, warnings) for r in (data.get("macro") or [])) if m]
    if not items and not macro:
        raise CatalystValidationError(
            "No usable catalysts found (need at least one valid item or macro entry).")

    ctx = CatalystContext(
        catalyst_date=_parse_iso(data.get("as_of"), warnings, "as_of"),
        generated_by=str(data.get("generated_by", "")).strip(),
        items=items, macro=macro,
        freeform_notes=str(data.get("freeform_notes", "")).strip(),
        found=True,
    )
    ctx.digest = _digest(ctx)
    return ctx, warnings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalysts.py -q`
Expected: PASS (all parser tests green).

- [ ] **Step 5: Commit**

```bash
git add src/advisory/catalysts.py tests/test_catalysts.py
git commit -m "feat(advisory): tolerant catalyst paste parser"
```

---

## Task 3: Catalyst file loader + context builder

**Files:**
- Modify: `src/advisory/catalysts.py` (add `find_latest_catalyst_file`, `build_catalyst_context`)
- Test: `tests/test_catalysts.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalysts.py`:

```python
from datetime import date
from src.advisory.catalysts import find_latest_catalyst_file, build_catalyst_context


def _write_brief(dir_path, d, body):
    cat_dir = dir_path / "catalysts"
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / f"catalyst-{d}.yaml").write_text(body, encoding="utf-8")


def test_find_latest_on_or_before(tmp_path):
    _write_brief(tmp_path, "2026-06-10", "items:\n  - {ticker: A, direction: bull, summary: x}\n")
    _write_brief(tmp_path, "2026-06-12", "items:\n  - {ticker: B, direction: bull, summary: y}\n")
    found = find_latest_catalyst_file(date(2026, 6, 11), data_dir=tmp_path)
    assert found is not None and found.name == "catalyst-2026-06-10.yaml"


def test_build_context_missing_is_graceful(tmp_path):
    ctx = build_catalyst_context(date(2026, 6, 12), data_dir=tmp_path)
    assert ctx.found is False


def test_build_context_near_term_and_staleness(tmp_path):
    _write_brief(tmp_path, "2026-06-10", (
        "as_of: 2026-06-10\n"
        "items:\n"
        "  - {ticker: NVDA, direction: bull, summary: earnings, event_date: 2026-06-12}\n"
        "  - {ticker: MU, direction: bear, summary: far, event_date: 2026-09-01}\n"
    ))
    ctx = build_catalyst_context(
        date(2026, 6, 11), data_dir=tmp_path,
        snapshot_date=date(2026, 6, 11), event_horizon_days=30,
    )
    assert ctx.found is True
    assert [i.ticker for i in ctx.near_term] == ["NVDA"]   # within 30d; MU excluded
    assert ctx.stale_vs_snapshot is True                   # 06-10 < snapshot 06-11
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalysts.py -q`
Expected: FAIL with `ImportError: cannot import name 'find_latest_catalyst_file'`.

- [ ] **Step 3: Add the loader + context builder**

Append to `src/advisory/catalysts.py`:

```python
def find_latest_catalyst_file(as_of: date, data_dir: str | Path = "data") -> Optional[Path]:
    """Return the newest data/catalysts/catalyst-YYYY-MM-DD.(yaml|yml) on/before as_of."""
    directory = Path(data_dir) / "catalysts"
    if not directory.exists():
        return None
    best: Optional[Path] = None
    best_date: Optional[date] = None
    for path in directory.glob("catalyst-*.y*ml"):
        match = _FILE_RE.search(path.name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date <= as_of and (best_date is None or file_date > best_date):
            best, best_date = path, file_date
    return best


def build_catalyst_context(
    as_of: date,
    data_dir: str | Path = "data",
    explicit_file: Optional[str | Path] = None,
    snapshot_date: Optional[date] = None,
    event_horizon_days: int = 30,
) -> CatalystContext:
    """Locate + load the dated brief; degrade gracefully when absent or unreadable."""
    path = Path(explicit_file) if explicit_file else find_latest_catalyst_file(as_of, data_dir)
    if path is None or not Path(path).exists():
        return CatalystContext(found=False)
    try:
        ctx, _warnings = parse_catalyst_paste(Path(path).read_text(encoding="utf-8", errors="replace"))
    except CatalystValidationError:
        return CatalystContext(found=False, path=str(path))

    ctx.path = str(path)
    match = _FILE_RE.search(Path(path).name)
    if match and not ctx.catalyst_date:
        ctx.catalyst_date = match.group(1)

    horizon_end = as_of + timedelta(days=event_horizon_days)
    near: List[CatalystItem] = []
    for item in ctx.items:
        if not item.event_date:
            continue
        try:
            ev = datetime.strptime(item.event_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if as_of <= ev <= horizon_end:
            near.append(item)
    ctx.near_term = near

    if ctx.catalyst_date and snapshot_date is not None:
        try:
            ctx.stale_vs_snapshot = datetime.strptime(ctx.catalyst_date, "%Y-%m-%d").date() < snapshot_date
        except ValueError:
            ctx.stale_vs_snapshot = False
    return ctx
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalysts.py -q`
Expected: PASS (all catalyst tests green).

- [ ] **Step 5: Commit**

```bash
git add src/advisory/catalysts.py tests/test_catalysts.py
git commit -m "feat(advisory): catalyst file loader + context builder"
```

---

## Task 4: Research-prompt generator

**Files:**
- Create: `src/prompt_engine/catalyst_prompt.py`
- Test: `tests/test_catalyst_prompt.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalyst_prompt.py`:

```python
from datetime import date
from src.prompt_engine.catalyst_prompt import generate_catalyst_prompt


def test_prompt_lists_tickers_events_and_schema():
    text = generate_catalyst_prompt(
        as_of=date(2026, 6, 12),
        held=[("NVDA", 12.5, 130.0), ("MU", 6.0, 95.0)],
        watchlist=["SMH", "GLDM"],
        events=[("2026-06-17", "June FOMC decision", "market")],
        generated_by_hint="perplexity",
    )
    # Held + watchlist tickers appear
    for t in ("NVDA", "MU", "SMH", "GLDM"):
        assert t in text
    # Upcoming event surfaced
    assert "June FOMC decision" in text
    # Schema instructions present (must request the exact YAML keys)
    for key in ("as_of:", "items:", "direction:", "summary:", "bull | bear | neutral"):
        assert key in text
    # ASCII-only (Windows-safe)
    assert text.encode("ascii", errors="strict")


def test_prompt_handles_no_events():
    text = generate_catalyst_prompt(
        as_of=date(2026, 6, 12), held=[("NVDA", 1.0, 1.0)],
        watchlist=[], events=[], generated_by_hint="claude",
    )
    assert "NVDA" in text
    assert "No calendar events" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalyst_prompt.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.prompt_engine.catalyst_prompt'`.

- [ ] **Step 3: Write the generator**

Create `src/prompt_engine/catalyst_prompt.py`:

```python
"""Render the daily catalyst research prompt (paste into an external LLM with web).

Pure/offline: it only renders already-loaded portfolio context into a structured ask.
The operator runs the result in Perplexity/Claude/ChatGPT and pastes the YAML back into
`catalyst-ingest`. ASCII-only output (Windows-safe).
"""

from __future__ import annotations

from datetime import date
from typing import List, Sequence, Tuple

from jinja2 import Template

CATALYST_PROMPT_TEMPLATE = Template("""\
You are a markets research assistant. Research TODAY's news and near-term catalysts for
the tickers below, using live web sources. Return ONLY a YAML document in EXACTLY the
schema shown at the end -- no preamble, no commentary outside the YAML.

Run date: {{ as_of }}
Generated-by hint (put in `generated_by`): {{ generated_by_hint }}

## Holdings (ticker | weight% | last price)
{% for t, w, p in held -%}
- {{ t }} | {{ "%.1f"|format(w) }}% | {{ "%.2f"|format(p) }}
{% endfor %}
{%- if watchlist %}

## Watchlist (sleeve names not currently held)
{% for t in watchlist -%}
- {{ t }}
{% endfor %}
{%- endif %}

## Known upcoming calendar events (for context; do not invent others)
{% if events -%}
{% for d, label, scope in events -%}
- {{ d }} | {{ scope }} | {{ label }}
{% endfor %}
{%- else -%}
No calendar events on file within the horizon.
{%- endif %}

## What to return
For EACH ticker above (held + watchlist), one `items` entry IF there is a real, sourced
catalyst; skip tickers with no news (do not fabricate). Add `macro` entries for
market-wide catalysts (rates/FOMC, broad tape, sector rotation). Use ONLY these enums:
direction = bull | bear | neutral; confidence = low | med | high. Cite a real
`source_url` for every claim. Dates are YYYY-MM-DD.

```yaml
as_of: {{ as_of }}
generated_by: {{ generated_by_hint }}
macro:
  - summary: "<market-wide catalyst>"
    direction: bull | bear | neutral
    event_date: YYYY-MM-DD        # optional
    source_url: "<url>"           # optional
items:
  - ticker: NVDA
    direction: bull | bear | neutral
    summary: "<one-line catalyst>"
    event_date: YYYY-MM-DD        # optional (earnings, launch, decision)
    confidence: low | med | high  # optional
    source_url: "<url>"           # optional
    notes: "<optional free text>"
freeform_notes: |
  <optional overall read of the tape>
```
""")


def generate_catalyst_prompt(
    *,
    as_of: date,
    held: Sequence[Tuple[str, float, float]],
    watchlist: Sequence[str],
    events: Sequence[Tuple[str, str, str]],
    generated_by_hint: str = "perplexity",
) -> str:
    """Render the research prompt. `held` = [(ticker, weight_pct, price)];
    `events` = [(iso_date, label, scope)]."""
    return CATALYST_PROMPT_TEMPLATE.render(
        as_of=as_of.isoformat(),
        held=list(held),
        watchlist=list(watchlist),
        events=list(events),
        generated_by_hint=generated_by_hint,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_catalyst_prompt.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/prompt_engine/catalyst_prompt.py tests/test_catalyst_prompt.py
git commit -m "feat(prompt): daily catalyst research-prompt generator"
```

---

## Task 5: `catalyst-prompt` CLI command

**Files:**
- Modify: `main.py` (add command near `daily-advisory`, after line ~2057)
- Test: `tests/test_cli.py` (extend)

First read the existing CLI test setup so the new test reuses the same fixtures/imports.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (use the existing `CliRunner` import already at the top of that file; if absent, add `from click.testing import CliRunner` and `from main import cli`):

```python
def test_catalyst_prompt_writes_file(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from main import cli
    runner = CliRunner()
    # Build a minimal snapshot on disk via the tracker the CLI uses, OR point --snapshot
    # at an existing fixture. Reuse the repo's snapshot fixture helper if one exists.
    # The command must not hit the network.
    result = runner.invoke(cli, [
        "catalyst-prompt", "--date", "2026-06-12",
        "--output-dir", str(tmp_path),
        "--held-only",
    ])
    # Either it wrote a prompt (snapshot present) or exited cleanly asking for a snapshot.
    assert result.exit_code in (0, 1)
    if result.exit_code == 0:
        written = list(tmp_path.glob("catalyst_research_*.txt"))
        assert written, "expected a catalyst_research_*.txt file"
        body = written[0].read_text(encoding="utf-8")
        assert "items:" in body and "direction:" in body
```

> Note for the implementer: if `tests/test_cli.py` already constructs a temp snapshot
> (look for a `sync` / `sync-bundle` invocation or a snapshot fixture), mirror that setup
> so `--date` resolves to a real snapshot and `exit_code == 0` is exercised. Keep the
> assertion tolerant of the no-snapshot path so the test is not environment-fragile.

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_cli.py -k catalyst_prompt -q`
Expected: FAIL with `Error: No such command 'catalyst-prompt'.`

- [ ] **Step 3: Add the command**

In `main.py`, after the `daily_advisory` command (after line ~2057, before `if __name__ == "__main__":`), add. Reuse existing imports already in `main.py`: `_load_config`, `PortfolioTracker`, `load_sleeve_universe`, `known_tickers`, `load_event_calendar`, `datetime`, `timedelta`, `Path`, `sys`, `click`. Add `from src.prompt_engine.catalyst_prompt import generate_catalyst_prompt` to the import block at the top of `main.py`.

```python
@cli.command("catalyst-prompt")
@click.option("--date", "as_of_text", default=None, help="Run date YYYY-MM-DD (default: snapshot date).")
@click.option("--snapshot", "snapshot_path", default=None, help="Snapshot to use (default: latest).")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml")
@click.option("--event-horizon-days", default=30, type=int)
@click.option("--held-only", is_flag=True, default=False, help="Only held tickers (no sleeve watchlist).")
@click.option("--generated-by", default="perplexity", help="Hint written into the prompt's generated_by.")
@click.option("--output-dir", default=None, help="Where to write the prompt (default: output/prompts).")
def catalyst_prompt(as_of_text, snapshot_path, universe_path, event_horizon_days,
                    held_only, generated_by, output_dir):
    """Emit a structured daily news/catalyst research prompt (paste into an external LLM)."""
    settings, _ctx = _load_config()
    tracker = PortfolioTracker(snapshots_dir=settings.get("snapshots_dir", "data/portfolio_snapshots"))
    try:
        snapshot = tracker.load_snapshot(snapshot_path) if snapshot_path else tracker.load_latest_snapshot()
    except FileNotFoundError:
        snapshot = None
    if snapshot is None:
        click.secho("No snapshot available. Run `sync` / `sync-bundle` first.", fg="red")
        sys.exit(1)

    as_of = datetime.strptime(as_of_text, "%Y-%m-%d").date() if as_of_text else snapshot.timestamp.date()
    try:
        _settings, sleeves = load_sleeve_universe(universe_path)
    except (FileNotFoundError, ValueError, KeyError):
        sleeves = []

    held_set = {p.ticker.upper() for p in snapshot.positions}
    held = [(p.ticker.upper(), p.weight_pct, p.current_price) for p in snapshot.positions]
    if held_only:
        watchlist = []
    else:
        watchlist = sorted(known_tickers(sleeves, snapshot) - held_set)

    horizon_end = as_of + timedelta(days=event_horizon_days)
    events = []
    try:
        for ev in load_event_calendar("config/event_calendar.yaml"):
            if as_of <= ev.event_date <= horizon_end:
                events.append((ev.event_date.isoformat(), ev.label, ev.scope))
    except Exception:  # noqa: BLE001 - calendar is optional context
        events = []

    text = generate_catalyst_prompt(
        as_of=as_of, held=held, watchlist=watchlist, events=events,
        generated_by_hint=generated_by,
    )
    out_dir = Path(output_dir or settings.get("output_dir", "output/prompts"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"catalyst_research_{as_of.isoformat()}.txt"
    out_path.write_text(text, encoding="utf-8")
    click.echo(f"Catalyst research prompt for {as_of.isoformat()} ({len(held)} held, "
               f"{len(watchlist)} watchlist, {len(events)} events):")
    click.echo(f"  {out_path}")
    click.echo("Paste it into Perplexity / Claude / ChatGPT, then run "
               "`catalyst-ingest --date " + as_of.isoformat() + " --file <pasted.txt>`.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_cli.py -k catalyst_prompt -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_cli.py
git commit -m "feat(cli): catalyst-prompt command"
```

---

## Task 6: `catalyst-ingest` CLI command

**Files:**
- Modify: `main.py` (add command after `catalyst-prompt`)
- Test: `tests/test_cli.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_catalyst_ingest_round_trip(tmp_path):
    from click.testing import CliRunner
    from main import cli
    runner = CliRunner()
    paste = tmp_path / "pasted.txt"
    paste.write_text(
        "as_of: 2026-06-12\n"
        "generated_by: perplexity\n"
        "items:\n"
        "  - {ticker: NVDA, direction: bull, summary: order}\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli, [
        "catalyst-ingest", "--date", "2026-06-12",
        "--file", str(paste), "--data-dir", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    out = tmp_path / "catalysts" / "catalyst-2026-06-12.yaml"
    assert out.exists()
    assert "NVDA" in out.read_text(encoding="utf-8")


def test_catalyst_ingest_bad_paste_exits_nonzero(tmp_path):
    from click.testing import CliRunner
    from main import cli
    runner = CliRunner()
    paste = tmp_path / "bad.txt"
    paste.write_text("items:\n  - {direction: bull, summary: no ticker}\n", encoding="utf-8")
    result = runner.invoke(cli, [
        "catalyst-ingest", "--date", "2026-06-12",
        "--file", str(paste), "--data-dir", str(tmp_path),
    ])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_cli.py -k catalyst_ingest -q`
Expected: FAIL with `Error: No such command 'catalyst-ingest'.`

- [ ] **Step 3: Add the command**

In `main.py`, after `catalyst_prompt`, add. Add `import yaml` at the top of `main.py` if not already imported, and `from src.advisory.catalysts import parse_catalyst_paste, CatalystValidationError`.

```python
@cli.command("catalyst-ingest")
@click.option("--date", "as_of_text", required=True, help="Brief date YYYY-MM-DD.")
@click.option("--file", "in_file", default=None, help="File with the pasted YAML (else read stdin).")
@click.option("--stdin", "use_stdin", is_flag=True, default=False, help="Read the paste from stdin.")
@click.option("--data-dir", default="data")
def catalyst_ingest(as_of_text, in_file, use_stdin, data_dir):
    """Validate a pasted catalyst brief and store data/catalysts/catalyst-<date>.yaml."""
    as_of = datetime.strptime(as_of_text, "%Y-%m-%d").date()
    if in_file:
        text = Path(in_file).read_text(encoding="utf-8")
    elif use_stdin or not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        click.secho("Provide --file PATH or pipe the paste via --stdin.", fg="red")
        sys.exit(1)

    try:
        ctx, warnings = parse_catalyst_paste(text)
    except CatalystValidationError as error:
        click.secho(f"Catalyst paste rejected: {error}", fg="red")
        sys.exit(1)

    payload = {
        "as_of": ctx.catalyst_date or as_of.isoformat(),
        "generated_by": ctx.generated_by,
        "macro": [m.to_dict() for m in ctx.macro],
        "items": [i.to_dict() for i in ctx.items],
        "freeform_notes": ctx.freeform_notes,
    }
    cat_dir = Path(data_dir) / "catalysts"
    cat_dir.mkdir(parents=True, exist_ok=True)
    out_path = cat_dir / f"catalyst-{as_of.isoformat()}.yaml"
    out_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")

    click.echo(f"Stored {out_path} ({len(ctx.items)} items, {len(ctx.macro)} macro).")
    for w in warnings:
        click.secho(f"  warning: {w}", fg="yellow")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_cli.py -k catalyst_ingest -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_cli.py
git commit -m "feat(cli): catalyst-ingest command"
```

---

## Task 7: Wire catalysts into `daily-advisory` (orchestrator + reporting + flags)

**Files:**
- Modify: `src/advisory/orchestrator.py` (accept + store `catalyst_context`)
- Modify: `src/advisory/reporting.py` ("Daily catalysts" section + catalyst column)
- Modify: `main.py` (`daily-advisory` loads the context; new flags; weave digest into prompt)
- Test: `tests/test_advisory_reporting.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_advisory_reporting.py` (reuse the module's existing `AdvisoryRun` import and any helper that builds a minimal run; if none, import directly):

```python
from src.advisory.models import (
    AdvisoryRun, BasketActionCandidate, CatalystContext, CatalystItem, MacroCatalyst,
)
from src.advisory.reporting import render_markdown


def _run_with_catalysts():
    return AdvisoryRun(
        as_of_date="2026-06-12", generated_at="t", snapshot_path="s",
        portfolio_value=16000.0, cash=1600.0, cash_pct=10.0, gate={"executable": False, "reason": "gated"},
        basket_actions=[BasketActionCandidate(
            basket="AI Platform", weight_pct=20.0, band_min_pct=15, band_max_pct=25,
            band_status="OK", verdict="HOLD", signal_ticker="NVDA")],
        catalysts=CatalystContext(
            found=True, catalyst_date="2026-06-12", generated_by="perplexity",
            macro=[MacroCatalyst(direction="bull", summary="rate-cut odds up")],
            items=[CatalystItem(ticker="NVDA", direction="bull", summary="hyperscaler order",
                                event_date="2026-06-18", confidence="med")],
            near_term=[CatalystItem(ticker="NVDA", direction="bull", summary="hyperscaler order",
                                    event_date="2026-06-18")],
        ),
    )


def test_report_renders_catalyst_section_and_column():
    md = render_markdown(_run_with_catalysts())
    assert "## 4b. Daily catalysts" in md
    assert "hyperscaler order" in md       # per-ticker row
    assert "rate-cut odds up" in md        # macro row
    assert "| Catalyst |" in md            # basket table gained the column
    md.encode("ascii", errors="strict")    # ASCII-only


def test_report_degrades_without_catalysts():
    run = _run_with_catalysts()
    run.catalysts = CatalystContext(found=False)
    md = render_markdown(run)
    assert "No catalyst brief" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_advisory_reporting.py -k catalyst -q`
Expected: FAIL — `## 4b. Daily catalysts` not present / `| Catalyst |` not in output.

- [ ] **Step 3a: Reporting — add the catalyst column to the basket table**

In `src/advisory/reporting.py`, section 3 (lines ~67-83), build a lookup before the loop and add the column. Replace the existing block:

```python
    # 3. Basket verdicts.
    a("## 3. Basket verdicts (add / trim / hold)")
    if run.basket_actions:
        a("| Basket | Weight | Band | Status | Verdict | Signal | Confidence | Note |")
        a("|---|---|---|---|---|---|---|---|")
        for c in run.basket_actions:
            band = (f"{c.band_min_pct}-{c.band_max_pct}%"
                    if c.band_min_pct is not None else "n/a")
            signal = "n/a" if c.signal_score is None else f"{c.signal_ticker} {c.signal_score:+.2f}"
            a(f"| {c.basket} | {c.weight_pct:.1f}% | {band} | {c.band_status} | "
              f"**{c.verdict}** | {signal} | {c.confidence or '-'} | {c.note} |")
```

with:

```python
    # 3. Basket verdicts.
    cat_by_ticker = {it.ticker.upper(): it for it in (run.catalysts.items if run.catalysts else [])}
    a("## 3. Basket verdicts (add / trim / hold)")
    if run.basket_actions:
        a("| Basket | Weight | Band | Status | Verdict | Signal | Confidence | Catalyst | Note |")
        a("|---|---|---|---|---|---|---|---|---|")
        for c in run.basket_actions:
            band = (f"{c.band_min_pct}-{c.band_max_pct}%"
                    if c.band_min_pct is not None else "n/a")
            signal = "n/a" if c.signal_score is None else f"{c.signal_ticker} {c.signal_score:+.2f}"
            hit = cat_by_ticker.get((c.signal_ticker or "").upper())
            catalyst = hit.direction if hit else "-"
            a(f"| {c.basket} | {c.weight_pct:.1f}% | {band} | {c.band_status} | "
              f"**{c.verdict}** | {signal} | {c.confidence or '-'} | {catalyst} | {c.note} |")
```

- [ ] **Step 3b: Reporting — add the "Daily catalysts" section**

In `src/advisory/reporting.py`, immediately AFTER the thesis block (after line ~96, before `# 5. Event timing.`), insert:

```python
    # 4b. Daily catalysts (news bridge).
    a("## 4b. Daily catalysts (news bridge, advisory)")
    cat = run.catalysts
    if cat and cat.found:
        stale = " (STALE vs snapshot)" if cat.stale_vs_snapshot else ""
        a(f"_Source: {cat.generated_by or 'n/a'} | {cat.catalyst_date or 'n/a'}{stale}. "
          "Narrative/context only; deterministic numbers unchanged._")
        a("")
        if cat.macro:
            a("**Macro:**")
            a("| Direction | Summary | Date | Source |")
            a("|---|---|---|---|")
            for m in cat.macro:
                a(f"| {m.direction} | {m.summary} | {m.event_date or '-'} | {m.source_url or '-'} |")
            a("")
        if cat.items:
            a("**Per-ticker:**")
            a("| Ticker | Direction | Summary | Date | Confidence | Source |")
            a("|---|---|---|---|---|---|")
            for it in cat.items:
                a(f"| {it.ticker} | {it.direction} | {it.summary} | {it.event_date or '-'} | "
                  f"{it.confidence or '-'} | {it.source_url or '-'} |")
            a("")
        if cat.near_term:
            a("**Near-term catalysts (reduce size into events):** "
              + ", ".join(f"{it.ticker} ({it.event_date})" for it in cat.near_term))
            a("")
        if cat.freeform_notes:
            a("> " + cat.freeform_notes.replace("\n", "\n> "))
            a("")
    else:
        a("_No catalyst brief for the run date - run `catalyst-prompt` / `catalyst-ingest`._")
    a("")
```

- [ ] **Step 4: Run reporting test to verify it passes**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_advisory_reporting.py -k catalyst -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Orchestrator — accept and store the context**

In `src/advisory/orchestrator.py`:

Add the import near the other advisory imports (top of file):

```python
from src.advisory.models import AdvisoryRun, OptionAdvisorySummary, CatalystContext
```

Add a parameter to `build_advisory_run(...)` (in the keyword-only signature, alongside `prompt_path`):

```python
    catalyst_context: Optional[CatalystContext] = None,
```

Where the `AdvisoryRun(...)` is constructed and returned at the end of the function, set the field (add to the constructor kwargs):

```python
        catalysts=catalyst_context or CatalystContext(),
```

> If `build_advisory_run` builds the run incrementally rather than in one constructor
> call, instead assign `run.catalysts = catalyst_context or CatalystContext()` before
> the `return`.

- [ ] **Step 6: main.py — load context, add flags, weave digest**

In `main.py` `daily_advisory`:

Add two options to the command decorator block (after `--thesis-file`, line ~1957):

```python
@click.option("--catalyst-file", default=None, help="Catalyst YAML override (default: latest by date).")
@click.option("--no-catalysts", is_flag=True, default=False, help="Skip the daily catalyst brief.")
```

Add the matching params to the `def daily_advisory(...)` signature: `catalyst_file, no_catalysts`.

Add the import at the top of `main.py`:

```python
from src.advisory.catalysts import build_catalyst_context
```

After `thesis_preview = build_thesis_context(...)` (line ~1994), add:

```python
    if no_catalysts:
        catalyst_ctx = None
    else:
        catalyst_ctx = build_catalyst_context(
            as_of, data_dir=data_dir, explicit_file=catalyst_file,
            snapshot_date=snapshot.timestamp.date(), event_horizon_days=event_horizon_days,
        )
        if not catalyst_ctx.found:
            notes.append("No catalyst brief found (run catalyst-prompt / catalyst-ingest).")
```

Pass it into `build_advisory_run(...)` (add to the existing kwargs near `thesis_file=...`):

```python
        catalyst_context=catalyst_ctx,
```

Weave the digest into the prompt question (inside the `if with_prompt:` block, replace the `question = ...` line, line ~2028):

```python
            question = run.thesis.digest[:400] if run.thesis.found else "Plan tomorrow's actions."
            if run.catalysts and run.catalysts.found and run.catalysts.digest:
                question += " | Catalysts: " + run.catalysts.digest[:400]
```

- [ ] **Step 7: Run the full advisory + CLI suites**

Run: `& .\.venv\Scripts\python.exe -m pytest tests/test_advisory_reporting.py tests/test_advisory_orchestrator.py tests/test_cli.py -q`
Expected: PASS (all green, including existing tests unaffected by the new column).

> If an existing orchestrator/reporting snapshot test asserts the old 8-column basket
> header, update that expected string to the new 9-column header (`| Catalyst |`). This
> is the only intended break.

- [ ] **Step 8: Commit**

```bash
git add src/advisory/orchestrator.py src/advisory/reporting.py main.py tests/test_advisory_reporting.py
git commit -m "feat(advisory): fold daily catalysts into daily-advisory"
```

---

## Task 8: Runbook update + gitignore + full-suite gate

**Files:**
- Modify: `.claude/skills/daily-review/SKILL.md`
- Modify: `.gitignore` (ensure `data/catalysts/` is ignored if `data/` is not already)

- [ ] **Step 1: Verify the catalyst dir is gitignored**

Run: `git check-ignore data/catalysts/x.yaml`
Expected: prints the path (already ignored via an existing `data/` rule).
If it prints NOTHING (not ignored), append to `.gitignore`:

```
# Daily catalyst briefs (local, human-curated; like snapshots/outputs)
data/catalysts/
```

- [ ] **Step 2: Document the news-bridge loop in the daily-review skill**

In `.claude/skills/daily-review/SKILL.md`, after the "Pick the mode" section (after line ~70, before "## When the review surfaces work"), insert:

```markdown
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
```

- [ ] **Step 3: Run the FULL test suite**

Run: `& .\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — all prior tests plus the new `test_catalysts.py`, `test_catalyst_prompt.py`, and the extended CLI/reporting tests. Confirm the count increased and nothing regressed.

- [ ] **Step 4: Smoke-test the loop end-to-end (manual, no network)**

Run:
```powershell
& .\.venv\Scripts\python.exe main.py catalyst-prompt --date 2026-06-12
& .\.venv\Scripts\python.exe main.py daily-advisory --date 2026-06-12 --no-network
```
Expected: the first writes `output/prompts/catalyst_research_2026-06-12.txt`; the second
runs clean and its markdown contains a "## 4b. Daily catalysts" section (showing
"No catalyst brief..." until you ingest one).

- [ ] **Step 5: Commit**

```bash
git add .gitignore .claude/skills/daily-review/SKILL.md
git commit -m "docs: document the daily catalyst news-bridge in the daily-review runbook"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- §3 data flow → Tasks 4-7 (prompt → ingest → advisory).
- §4 light schema → Task 2 parser + Task 6 storage (canonical YAML).
- §5.1 models → Task 1. §5.2 loader/parser → Tasks 2-3. §5.3 prompt → Task 4.
  §5.4 CLI commands → Tasks 5-6. §5.5 daily-advisory wiring → Task 7. §5.6 runbook → Task 8.
- §6 governance (S3 intact, numbers untouched, ASCII) → enforced in Tasks 2/4/7 tests.
- §7 degradation → Task 3 (missing/stale graceful), Task 2 (bad blocks), Task 7 (degrade test).
- §8 testing → tests in every task; §9 files → File Structure table; §10 out-of-scope → respected (no scheduler).

**Placeholder scan:** No "TBD"/"add error handling"-style steps; every code step shows full code. The only deliberately conditional steps are the two `> Note` blocks in Tasks 5 and 7, which tell the implementer to mirror existing fixtures / update one snapshot string — these are guidance about *existing* code the plan cannot see verbatim, not missing plan content.

**Type consistency:** `CatalystItem`/`MacroCatalyst`/`CatalystContext` field names are identical across Tasks 1, 2, 3, 6, 7 (`ticker`, `direction`, `summary`, `event_date`, `confidence`, `source_url`, `notes`; context `items`/`macro`/`near_term`/`catalyst_date`/`generated_by`/`freeform_notes`/`digest`/`stale_vs_snapshot`/`found`). `generate_catalyst_prompt` keyword signature matches between Task 4 (def) and Task 5 (call). `parse_catalyst_paste` returns `(ctx, warnings)` everywhere it is used. `build_catalyst_context` signature matches between Task 3 (def) and Tasks 7 call site.
