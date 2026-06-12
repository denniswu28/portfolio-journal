"""Load the latest Boist thesis markdown as narrative overlay (no number extraction).

Picks the newest ``data/boist-YYYY-MM-DD.md`` dated on or before the run date, reads it
as plain text, and extracts a verbatim digest + mentioned tickers via light regex.
Per AGENTS.md §3, the thesis is overlay only — its price levels are never promoted to
deterministic targets here.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Set

from src.advisory.models import ThesisContext

_FILE_RE = re.compile(r"boist-(\d{4}-\d{2}-\d{2})\.md$", re.IGNORECASE)
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")

# Common all-caps words that look like tickers but are not.
_STOPWORDS: Set[str] = {
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAS", "HAD", "WAS",
    "USD", "CEO", "CFO", "IPO", "FED", "FOMC", "GDP", "CPI", "PCE", "NFP", "ETF", "ETFS",
    "AI", "ML", "USA", "US", "EU", "UK", "Q1", "Q2", "Q3", "Q4", "YOY", "QOQ", "TBD",
    "DTE", "POP", "TP", "SL", "OK", "NA", "ATH", "DRAM", "NAND", "HBM", "CSP", "CSPS",
    "PM", "ET", "EST", "PDT", "YE", "QT", "QE",
}


def find_latest_thesis(as_of: date, data_dir: str | Path = "data") -> Optional[Path]:
    """Return the newest boist-*.md dated on or before ``as_of`` (None if none)."""
    directory = Path(data_dir)
    if not directory.exists():
        return None
    best: Optional[Path] = None
    best_date: Optional[date] = None
    for path in directory.glob("boist-*.md"):
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


def _extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip().strip("*").strip()
        if stripped:
            return stripped[:200]
    return ""


def _extract_digest(text: str, max_chars: int = 1600) -> str:
    """Title + first substantive paragraph + a trading-plan section, verbatim."""
    lines = text.splitlines()
    parts: List[str] = []
    # First non-empty paragraph.
    for line in lines:
        if line.strip():
            parts.append(line.strip())
        elif parts:
            break
    # A "plan"/"trade"/"action" section heading and the lines under it.
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and re.search(r"plan|trade|action", line, re.IGNORECASE):
            section = [line.strip()]
            for follow in lines[i + 1 : i + 9]:
                if follow.lstrip().startswith("#"):
                    break
                if follow.strip():
                    section.append(follow.strip())
            parts.append("\n".join(section))
            break
    digest = "\n\n".join(parts).strip()
    return digest[:max_chars]


def _extract_tickers(text: str, known: Optional[Set[str]] = None, limit: int = 25) -> List[str]:
    found: List[str] = []
    for token in _TICKER_RE.findall(text):
        if token in _STOPWORDS:
            continue
        if known is not None and token not in known:
            continue
        if token not in found:
            found.append(token)
    return found[:limit]


def build_thesis_context(
    as_of: date,
    data_dir: str | Path = "data",
    explicit_file: Optional[str | Path] = None,
    snapshot_date: Optional[date] = None,
    known_tickers: Optional[Set[str]] = None,
) -> ThesisContext:
    """Build the ThesisContext for a run date (graceful when no thesis exists)."""
    path = Path(explicit_file) if explicit_file else find_latest_thesis(as_of, data_dir)
    if path is None or not Path(path).exists():
        return ThesisContext(found=False, title="No thesis found on or before run date.")

    text = Path(path).read_text(encoding="utf-8", errors="replace")
    match = _FILE_RE.search(Path(path).name)
    thesis_date = match.group(1) if match else None
    stale = False
    if thesis_date and snapshot_date is not None:
        try:
            stale = datetime.strptime(thesis_date, "%Y-%m-%d").date() < snapshot_date
        except ValueError:
            stale = False

    return ThesisContext(
        path=str(path),
        thesis_date=thesis_date,
        title=_extract_title(text),
        digest=_extract_digest(text),
        tickers=_extract_tickers(text, known=known_tickers),
        stale_vs_snapshot=stale,
        found=True,
    )
