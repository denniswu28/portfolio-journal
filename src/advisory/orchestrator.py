"""Assemble the deterministic core of the daily advisory packet.

Network-dependent pieces (live option chains, position re-marks, LLM prompt) are
computed by the CLI and passed in via ``option_summary`` / ``metrics`` / ``prompt_path``;
everything here is pure and testable offline. Reuses the existing basket, rule, thesis,
event, and gating components — it reimplements nothing.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Set

from src.advisory.gating import options_gate_status
from src.advisory.models import AdvisoryRun, CatalystContext, OptionAdvisorySummary
from src.advisory.rules import basket_action_candidates, evaluate_rules
from src.advisory.signal_overlay import enrich_basket_actions
from src.advisory.thesis import build_thesis_context
from src.data_ingestion.models import PersistentContext, PortfolioSnapshot
from src.options.events import load_event_calendar
from src.portfolio.baskets import build_baskets, compute_basket_metrics
from src.portfolio.optimizer import SleeveDefinition


def known_tickers(sleeves: List[SleeveDefinition], snapshot: PortfolioSnapshot) -> Set[str]:
    """Universe of recognizable tickers (sleeve proxies + holdings + held positions)."""
    known: Set[str] = set()
    for sleeve in sleeves or []:
        known.add(sleeve.proxy.upper())
        known.update(h.upper() for h in sleeve.holdings)
    for position in snapshot.positions:
        known.add(position.ticker.upper())
    return known


def _horizon_events(event_calendar_path, as_of: date, horizon_days: int, notes: List[str]):
    events = []
    try:
        for event in load_event_calendar(event_calendar_path):
            if as_of <= event.event_date <= as_of + timedelta(days=horizon_days):
                events.append({
                    "date": event.event_date.isoformat(),
                    "label": event.label,
                    "scope": event.scope,
                })
    except Exception as error:  # noqa: BLE001 - degrade gracefully, never abort the brief
        notes.append(f"Event calendar unavailable: {error}")
    return sorted(events, key=lambda e: e["date"])


def build_advisory_run(
    *,
    as_of: date,
    snapshot: PortfolioSnapshot,
    snapshot_path: Optional[str],
    ctx: PersistentContext,
    sleeves: List[SleeveDefinition],
    generated_at: str,
    data_dir: str = "data",
    thesis_file: Optional[str] = None,
    event_calendar_path: str = "config/event_calendar.yaml",
    event_horizon_days: int = 30,
    account_value: Optional[float] = None,
    account_id: Optional[str] = None,
    option_summary: Optional[OptionAdvisorySummary] = None,
    metrics: Optional[dict] = None,
    prompt_path: Optional[str] = None,
    signals_by_ticker: Optional[dict] = None,
    extra_notes: Optional[List[str]] = None,
    catalyst_context: Optional[CatalystContext] = None,
) -> AdvisoryRun:
    """Build the AdvisoryRun from a snapshot and already-loaded config/inputs."""
    notes: List[str] = list(extra_notes or [])
    total = snapshot.total_portfolio_value or 0.0

    # Basket decomposition + per-basket metrics (degrade if no sleeve match).
    try:
        view = build_baskets(snapshot, sleeves)
        basket_metrics = compute_basket_metrics(view)
        mismatches = view.mismatches
    except Exception as error:  # noqa: BLE001
        basket_metrics, mismatches = [], []
        notes.append(f"Basket decomposition skipped: {error}")

    rule_alerts = evaluate_rules(snapshot, basket_metrics=basket_metrics, mismatches=mismatches)

    snapshot_date = snapshot.timestamp.date() if snapshot.timestamp else as_of
    thesis = build_thesis_context(
        as_of, data_dir=data_dir, explicit_file=thesis_file,
        snapshot_date=snapshot_date, known_tickers=known_tickers(sleeves, snapshot),
    )

    basket_actions = basket_action_candidates(basket_metrics, set(thesis.tickers))
    if signals_by_ticker:
        representative = {row.get("basket"): row.get("top_holding") for row in basket_metrics}
        basket_actions = enrich_basket_actions(basket_actions, signals_by_ticker, representative)

    events = _horizon_events(event_calendar_path, as_of, event_horizon_days, notes)

    gate = options_gate_status(ctx, account_value if account_value is not None else total, account_id)
    if option_summary is None:
        option_summary = OptionAdvisorySummary(
            gated=not gate.executable,
            gate_reason=gate.reason,
            candidates=[],
            open_position_alerts=[],
            note="No live option data requested.",
        )
    else:
        option_summary.gated = not gate.executable
        option_summary.gate_reason = gate.reason

    cash_pct = (snapshot.cash / total * 100.0) if total else 0.0

    return AdvisoryRun(
        as_of_date=as_of.isoformat(),
        generated_at=generated_at,
        snapshot_path=snapshot_path,
        portfolio_value=total,
        cash=snapshot.cash,
        cash_pct=cash_pct,
        gate={"executable": gate.executable, "reason": gate.reason,
              "account_value": gate.account_value, "min_required": gate.min_required,
              "privilege_pending": gate.privilege_pending},
        rule_alerts=rule_alerts,
        basket_actions=basket_actions,
        thesis=thesis,
        catalysts=catalyst_context or CatalystContext(),
        events=events,
        options=option_summary,
        metrics=metrics or {},
        prompt_path=prompt_path,
        notes=notes,
    )
