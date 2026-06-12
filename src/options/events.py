"""
events.py - Structured macro/earnings event calendar for monitor signals.

Events enter the system only through this calendar (and the dated thesis markdown);
nothing scrapes the web. The monitor uses upcoming events to flag positions for size
reduction or defensive rolls around catalysts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class MarketEvent:
    """A dated catalyst. ``scope`` is 'market' or a specific ticker symbol."""

    event_date: date
    label: str
    scope: str = "market"

    @property
    def is_market(self) -> bool:
        return self.scope.strip().lower() == "market"


def load_event_calendar(path: str | Path = "config/event_calendar.yaml") -> List[MarketEvent]:
    """Load the event calendar YAML; returns an empty list if absent or empty."""
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    events: List[MarketEvent] = []
    for raw in data.get("events", []) or []:
        try:
            ev_date = raw["date"]
            if not isinstance(ev_date, date):
                ev_date = date.fromisoformat(str(ev_date))
            events.append(MarketEvent(
                event_date=ev_date,
                label=str(raw.get("label", "")),
                scope=str(raw.get("scope", "market")),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(events, key=lambda e: e.event_date)


def relevant_events(
    events: List[MarketEvent],
    underlying: str,
    eval_date: Optional[date] = None,
    horizon_days: int = 14,
) -> List[MarketEvent]:
    """Return market-wide and underlying-specific events within the horizon."""
    eval_date = eval_date or date.today()
    horizon = eval_date + timedelta(days=horizon_days)
    token = underlying.strip().upper()
    out = []
    for ev in events:
        if ev.event_date < eval_date or ev.event_date > horizon:
            continue
        if ev.is_market or ev.scope.strip().upper() == token:
            out.append(ev)
    return out
