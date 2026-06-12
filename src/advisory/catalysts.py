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
