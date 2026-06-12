"""Typed, serializable results for the daily advisory packet."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

# Severity ordering for sorting (ACTION first).
SEVERITY_ORDER = {"ACTION": 0, "WARN": 1, "INFO": 2}


@dataclass
class RuleAlert:
    """One portfolio-rule finding (constraint breach, TP/SL flag, band drift)."""

    severity: str   # ACTION | WARN | INFO
    category: str   # cash | allocation | position_cap | take_profit | stop_loss | band | mismatch | out_of_basket
    title: str
    detail: str
    ticker: Optional[str] = None
    basket: Optional[str] = None
    value: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BasketActionCandidate:
    """An add/trim/hold verdict for a basket, derived from band + thesis + signals."""

    basket: str
    weight_pct: float
    band_min_pct: Optional[float]
    band_max_pct: Optional[float]
    band_status: str   # OK | ABOVE | BELOW | N/A
    verdict: str       # ADD | TRIM | HOLD
    note: str = ""
    # Technical-signal overlay (Phase 4); None/empty when signals unavailable.
    signal_ticker: Optional[str] = None
    signal_score: Optional[float] = None   # composite overall, -1..1
    signal_flags: List[str] = field(default_factory=list)
    confidence: str = ""                    # confirmed | counter-trend | into-strength | *-watch | neutral

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OptionAdvisorySummary:
    """Gated option ideas + open-position monitor outcome."""

    gated: bool
    gate_reason: str
    candidates: List[dict] = field(default_factory=list)         # fully-specified tickets, each labeled
    open_position_alerts: List[dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThesisContext:
    """Narrative overlay extracted from the latest boist markdown (verbatim)."""

    path: Optional[str] = None
    thesis_date: Optional[str] = None
    title: str = ""
    digest: str = ""
    tickers: List[str] = field(default_factory=list)
    stale_vs_snapshot: bool = False
    found: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


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
    """Loaded daily catalyst brief -- the daily-news analog of ThesisContext."""

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


@dataclass
class AdvisoryRun:
    """The full daily advisory packet (rendered to markdown + JSON)."""

    as_of_date: str
    generated_at: str
    snapshot_path: Optional[str]
    portfolio_value: float
    cash: float
    cash_pct: float
    gate: dict
    rule_alerts: List[RuleAlert] = field(default_factory=list)
    basket_actions: List[BasketActionCandidate] = field(default_factory=list)
    thesis: ThesisContext = field(default_factory=ThesisContext)
    catalysts: CatalystContext = field(default_factory=CatalystContext)
    events: List[dict] = field(default_factory=list)
    options: Optional[OptionAdvisorySummary] = None
    metrics: dict = field(default_factory=dict)
    prompt_path: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        # Sort alerts by severity for stable, action-first output.
        data["rule_alerts"] = sorted(
            data["rule_alerts"], key=lambda a: SEVERITY_ORDER.get(a["severity"], 9)
        )
        return data
