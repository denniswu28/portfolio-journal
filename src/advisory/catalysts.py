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
    # NOTE (deferred): the spec also wants unknown tickers (not in the held/sleeve
    # universe) flagged in warnings. That needs a known-universe set threaded in from
    # the caller; deferred since catalysts are display-only. Catalysts never affect
    # deterministic numbers, so an unrecognized ticker is harmless context.
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
    try:
        data = yaml.safe_load(_strip_fences(text)) or {}
    except yaml.YAMLError as exc:
        raise CatalystValidationError(f"Catalyst paste is not valid YAML: {exc}") from exc
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
    except (CatalystValidationError, OSError):
        # OSError covers IsADirectoryError / PermissionError / a delete-after-exists race:
        # an unreadable brief must degrade, never crash the daily advisory run.
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
    # ctx.digest was set by parse_catalyst_paste above and reflects all items (not
    # near_term only); rebuild via _digest if a near-term-only digest is ever needed.
    ctx.near_term = near

    if ctx.catalyst_date and snapshot_date is not None:
        try:
            ctx.stale_vs_snapshot = datetime.strptime(ctx.catalyst_date, "%Y-%m-%d").date() < snapshot_date
        except ValueError:
            ctx.stale_vs_snapshot = False
    return ctx
