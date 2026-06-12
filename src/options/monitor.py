"""
monitor.py - Semi-automated daily option position monitor (no execution).

Re-marks open option positions deterministically (QuantLib), then evaluates the
exit/roll/assignment/event rules from AGENTS.md and emits alerts plus recommended
orders. It writes a dated report; a human places any trades.

Rules per position:
  * TAKE_PROFIT - P&L reaches the take-profit threshold (default +50% of risk base).
  * STOP_LOSS   - loss reaches the stop threshold (debit -50% / credit -1x credit).
  * TIME_STOP   - DTE at/under the close-by threshold (default 21 DTE).
  * ASSIGNMENT  - a short leg is in-the-money.
  * EVENT       - a calendar catalyst falls inside the alert horizon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.options.events import MarketEvent, relevant_events
from src.options.models import OptionPosition
from src.options.strategies import mark_strategy

TAKE_PROFIT = "TAKE_PROFIT"
STOP_LOSS = "STOP_LOSS"
TIME_STOP = "TIME_STOP"
ASSIGNMENT = "ASSIGNMENT"
EVENT = "EVENT"
NO_DATA = "NO_DATA"

DEFAULT_TAKE_PROFIT_PCT = 0.50
DEFAULT_DEBIT_STOP_PCT = 0.50
DEFAULT_CREDIT_STOP_PCT = 1.00
DEFAULT_CLOSE_BY_DTE = 21
_SEVERITY_RANK = {"ACTION": 0, "WARN": 1, "INFO": 2}


@dataclass
class Alert:
    position_id: str
    underlying: str
    kind: str
    severity: str            # ACTION | WARN | INFO
    message: str
    recommended_action: str = ""


@dataclass
class PositionMonitor:
    position_id: str
    underlying: str
    structure: str
    mark: float
    pnl: float
    pnl_pct: float
    dte: int
    is_credit: bool
    alerts: List[Alert] = field(default_factory=list)


def _short_legs_itm(position: OptionPosition, spot: float) -> List[str]:
    breached = []
    for leg in position.strategy.legs:
        if leg.action != "SELL":
            continue
        if leg.is_call() and spot > leg.strike:
            breached.append(f"short {leg.strike:g} CALL ITM")
        elif leg.is_put() and spot < leg.strike:
            breached.append(f"short {leg.strike:g} PUT ITM")
    return breached


def monitor_position(
    position: OptionPosition,
    spot: Optional[float],
    rate: float,
    vol: Optional[float],
    eval_date: Optional[date] = None,
    events: Optional[Sequence[MarketEvent]] = None,
    event_horizon_days: int = 14,
) -> PositionMonitor:
    """Evaluate one open position against all monitor rules."""
    eval_date = eval_date or date.today()
    nearest_expiry = min(leg.expiry for leg in position.strategy.legs)
    dte = (nearest_expiry - eval_date).days

    if spot is None or vol is None:
        pm = PositionMonitor(position.id, position.underlying, position.strategy.name,
                             0.0, 0.0, 0.0, dte, position.is_credit)
        pm.alerts.append(Alert(position.id, position.underlying, NO_DATA, "WARN",
                               "No spot/vol available to mark this position.", "Refresh market data."))
        return pm

    mark = mark_strategy(position.strategy, spot, rate, vol, eval_date=eval_date)
    pnl = mark - position.entry_net_debit
    risk_base = position.risk_base or 1.0
    pnl_pct = pnl / risk_base
    is_credit = position.is_credit

    pm = PositionMonitor(position.id, position.underlying, position.strategy.name,
                         round(mark, 2), round(pnl, 2), round(pnl_pct, 4), dte, is_credit)

    tp = position.take_profit_pct if position.take_profit_pct is not None else DEFAULT_TAKE_PROFIT_PCT
    sl = position.stop_loss_pct if position.stop_loss_pct is not None else (
        DEFAULT_CREDIT_STOP_PCT if is_credit else DEFAULT_DEBIT_STOP_PCT
    )
    close_by = position.close_by_dte if position.close_by_dte is not None else DEFAULT_CLOSE_BY_DTE

    if pnl_pct >= tp:
        pm.alerts.append(Alert(position.id, position.underlying, TAKE_PROFIT, "ACTION",
                               f"P&L {pnl_pct * 100:.0f}% of risk base >= take-profit {tp * 100:.0f}%.",
                               "Close to realize profit."))
    if pnl_pct <= -sl:
        pm.alerts.append(Alert(position.id, position.underlying, STOP_LOSS, "ACTION",
                               f"P&L {pnl_pct * 100:.0f}% of risk base <= stop {-sl * 100:.0f}%.",
                               "Close or defend/roll to cut the loss."))
    if dte <= close_by:
        pm.alerts.append(Alert(position.id, position.underlying, TIME_STOP, "WARN",
                               f"{dte} DTE <= close-by {close_by} DTE.",
                               "Close or roll out; avoid expiration-week gamma."))
    for breach in _short_legs_itm(position, spot):
        pm.alerts.append(Alert(position.id, position.underlying, ASSIGNMENT, "WARN",
                               f"Assignment risk: {breach} (spot {spot:g}).",
                               "Monitor for early assignment; roll if undesired."))
    for ev in relevant_events(list(events or []), position.underlying, eval_date, event_horizon_days):
        pm.alerts.append(Alert(position.id, position.underlying, EVENT, "INFO",
                               f"Upcoming {ev.event_date.isoformat()}: {ev.label}.",
                               "Consider reducing size or adding protection into the event."))
    return pm


def monitor_positions(
    positions: Sequence[OptionPosition],
    spot_map: Dict[str, float],
    rate: float,
    vol_map: Dict[str, float],
    eval_date: Optional[date] = None,
    events: Optional[Sequence[MarketEvent]] = None,
    event_horizon_days: int = 14,
) -> List[PositionMonitor]:
    """Evaluate all open positions."""
    return [
        monitor_position(
            pos, spot_map.get(pos.underlying), rate, vol_map.get(pos.underlying),
            eval_date=eval_date, events=events, event_horizon_days=event_horizon_days,
        )
        for pos in positions
    ]


def all_alerts(monitors: Sequence[PositionMonitor]) -> List[Alert]:
    """Flatten and sort alerts by severity (ACTION first)."""
    alerts = [a for m in monitors for a in m.alerts]
    return sorted(alerts, key=lambda a: _SEVERITY_RANK.get(a.severity, 9))


def write_monitor_report(
    monitors: Sequence[PositionMonitor],
    output_path: str | Path,
    risk: Optional[object] = None,
    stress: Optional[List[dict]] = None,
    sleeve: Optional[dict] = None,
    basket_alerts: Optional[List[str]] = None,
    context_notes: Optional[List[str]] = None,
) -> Path:
    """Render the daily monitor report (alerts, marks, risk, stress) to markdown."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = ["# Options & Basket Monitor", ""]
    lines.append("Educational planning note, not financial advice. Alerts and recommended "
                 "orders are deterministic; a human reviews and places every trade.")
    lines.append("")
    for note in context_notes or []:
        lines.append(f"- {note}")
    if context_notes:
        lines.append("")

    actions = all_alerts(monitors)
    if actions:
        lines.append("## Alerts (highest severity first)")
        lines.append("")
        lines.append("| Severity | Kind | Underlying | Message | Recommended |")
        lines.append("|---|---|---|---|---|")
        for a in actions:
            lines.append(f"| {a.severity} | {a.kind} | {a.underlying} | {a.message} | {a.recommended_action} |")
        lines.append("")
    else:
        lines.append("No position alerts triggered.\n")

    lines.append("## Open positions")
    lines.append("")
    lines.append("| Position | Underlying | Structure | Mark | P&L | P&L % | DTE |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for m in monitors:
        lines.append(
            f"| {m.position_id} | {m.underlying} | {m.structure} | ${m.mark:,.2f} | "
            f"${m.pnl:,.2f} | {m.pnl_pct * 100:.0f}% | {m.dte} |"
        )
    lines.append("")

    if sleeve:
        lines.append("## Options sleeve")
        lines.append("")
        lines.append(f"- Options value: ${sleeve['options_value']:,.2f} of "
                     f"${sleeve['portfolio_value']:,.2f} = {sleeve['weight_pct']:.2f}% "
                     f"(target {sleeve['target_pct']:.0f}%, status {sleeve['status']}).")
        lines.append("")

    if risk is not None:
        lines.append("## Aggregate option greeks")
        lines.append("")
        lines.append(f"- Net delta: {risk.net_delta:.1f} (dollar-delta ${risk.dollar_delta:,.0f})")
        lines.append(f"- Net gamma: {risk.net_gamma:.2f} | Net theta/day: ${risk.net_theta:,.0f}")
        lines.append(f"- Net vega/vol-pt: ${risk.net_vega:,.0f} | Net rho/1%: ${risk.net_rho:,.0f}")
        lines.append("")

    if stress:
        lines.append("## Spot stress scenarios (change in option-book value)")
        lines.append("")
        lines.append("| Spot shock | Vol shock | Book P&L |")
        lines.append("|---:|---:|---:|")
        for row in stress:
            lines.append(f"| {row['spot_shock_pct']:+.0f}% | {row['vol_shock_pct']:+.0f}% | ${row['pnl']:,.2f} |")
        lines.append("")

    if basket_alerts:
        lines.append("## Basket drift")
        lines.append("")
        for ba in basket_alerts:
            lines.append(f"- {ba}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
