"""Deterministic portfolio-rule evaluation -> typed RuleAlerts + basket verdicts.

Encodes the hard constraints from ``config/persistent_context.yaml`` (10% single-equity
cap, ~80/10/10 allocation, take-profit +50%, stop-loss -20%) and the sleeve policy
bands as structured alerts. No judgment, no LLM — just thresholds over the snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set

from src.advisory.models import BasketActionCandidate, RuleAlert
from src.data_ingestion.models import PortfolioSnapshot, Position

# Gold / precious-metal hedges exempt from the single-equity cap when documented.
GOLD_PM_TICKERS: Set[str] = {
    "GLD", "GLDM", "IAU", "IAUI", "SGOL", "SLV", "PPLT", "PLTM", "GOLD", "IAUM",
}


@dataclass(frozen=True)
class RuleThresholds:
    position_cap_pct: float = 10.0
    take_profit_pct: float = 50.0
    stop_loss_pct: float = -20.0
    cash_target_pct: float = 10.0
    cash_band_pct: float = 5.0
    long_target_pct: float = 80.0


def _weight(position: Position, total: float) -> float:
    if position.weight_pct:
        return position.weight_pct
    return (position.market_value / total * 100.0) if total else 0.0


def _is_gold_pm(position: Position) -> bool:
    if position.ticker.upper() in GOLD_PM_TICKERS:
        return True
    name = (position.basket_name or "").lower()
    return "gold" in name or "precious" in name


def evaluate_rules(
    snapshot: PortfolioSnapshot,
    basket_metrics: Optional[List[dict]] = None,
    mismatches: Optional[List[str]] = None,
    thresholds: RuleThresholds = RuleThresholds(),
) -> List[RuleAlert]:
    """Return all rule alerts for a snapshot (action-first severity assigned per rule)."""
    alerts: List[RuleAlert] = []
    total = snapshot.total_portfolio_value or 0.0

    for position in snapshot.positions:
        weight = _weight(position, total)
        pnl_pct = position.unrealized_pnl_pct

        if weight > thresholds.position_cap_pct and not _is_gold_pm(position):
            alerts.append(RuleAlert(
                severity="ACTION", category="position_cap",
                title=f"{position.ticker} exceeds {thresholds.position_cap_pct:.0f}% cap",
                detail=f"{position.ticker} is {weight:.1f}% of the portfolio "
                       f"(cap {thresholds.position_cap_pct:.0f}%). Trim to comply.",
                ticker=position.ticker, basket=position.basket_name, value=round(weight, 2),
            ))
        if pnl_pct >= thresholds.take_profit_pct:
            alerts.append(RuleAlert(
                severity="ACTION", category="take_profit",
                title=f"{position.ticker} hit take-profit (+{thresholds.take_profit_pct:.0f}%)",
                detail=f"{position.ticker} unrealized {pnl_pct:+.1f}%. Rule: take profits "
                       f">= +{thresholds.take_profit_pct:.0f}%.",
                ticker=position.ticker, basket=position.basket_name, value=round(pnl_pct, 2),
            ))
        elif pnl_pct <= thresholds.stop_loss_pct:
            alerts.append(RuleAlert(
                severity="ACTION", category="stop_loss",
                title=f"{position.ticker} hit stop-loss ({thresholds.stop_loss_pct:.0f}%)",
                detail=f"{position.ticker} unrealized {pnl_pct:+.1f}%. Rule: cut losses "
                       f"at {thresholds.stop_loss_pct:.0f}%.",
                ticker=position.ticker, basket=position.basket_name, value=round(pnl_pct, 2),
            ))

    # Cash vs target.
    cash_pct = (snapshot.cash / total * 100.0) if total else 0.0
    if abs(cash_pct - thresholds.cash_target_pct) > thresholds.cash_band_pct:
        direction = "above" if cash_pct > thresholds.cash_target_pct else "below"
        action = "deploy excess cash" if direction == "above" else "raise cash"
        alerts.append(RuleAlert(
            severity="WARN", category="cash",
            title=f"Cash {cash_pct:.1f}% is {direction} the {thresholds.cash_target_pct:.0f}% target",
            detail=f"Cash is {cash_pct:.1f}% vs {thresholds.cash_target_pct:.0f}% target; consider to {action}.",
            value=round(cash_pct, 2),
        ))

    # Allocation summary (long vs 80%).
    invested_pct = (snapshot.invested_value / total * 100.0) if total else 0.0
    alerts.append(RuleAlert(
        severity="INFO", category="allocation",
        title="Allocation snapshot",
        detail=f"Long {invested_pct:.1f}% / cash {cash_pct:.1f}% "
               f"(target ~{thresholds.long_target_pct:.0f}% long / {thresholds.cash_target_pct:.0f}% cash; "
               f"options sleeve tracked separately).",
        value=round(invested_pct, 2),
    ))

    # Basket band drift.
    for row in basket_metrics or []:
        status = row.get("band_status")
        if status in ("ABOVE", "BELOW"):
            band = f"{row.get('band_min_pct')}-{row.get('band_max_pct')}%"
            alerts.append(RuleAlert(
                severity="WARN", category="band",
                title=f"{row.get('basket')} is {status} its policy band",
                detail=f"{row.get('basket')} at {row.get('weight_pct')}% vs band {band}. "
                       f"{'Trim' if status == 'ABOVE' else 'Add'} to return within band.",
                basket=row.get("basket"), value=row.get("weight_pct"),
            ))

    # Sleeve mismatches.
    for mismatch in mismatches or []:
        alerts.append(RuleAlert(
            severity="INFO", category="mismatch", title="Sleeve mismatch", detail=str(mismatch),
        ))

    # Out-of-basket holdings.
    orphan = [p.ticker for p in snapshot.positions if not p.basket_name]
    if orphan:
        alerts.append(RuleAlert(
            severity="INFO", category="out_of_basket",
            title=f"{len(orphan)} out-of-basket holding(s)",
            detail="Edit individually (never via a basket method): " + ", ".join(orphan),
        ))

    return alerts


def basket_action_candidates(
    basket_metrics: Optional[List[dict]],
    thesis_tickers: Optional[Set[str]] = None,
) -> List[BasketActionCandidate]:
    """Add/trim/hold verdicts from policy band, annotated with thesis relevance."""
    thesis_tickers = {t.upper() for t in (thesis_tickers or set())}
    candidates: List[BasketActionCandidate] = []
    for row in basket_metrics or []:
        status = row.get("band_status", "N/A")
        if status == "ABOVE":
            verdict, note = "TRIM", "Above policy band — trim toward band (Method A/B)."
        elif status == "BELOW":
            verdict, note = "ADD", "Below policy band — add toward band (Method A/B)."
        else:
            verdict, note = "HOLD", "Within band."
        top = str(row.get("top_holding", "")).upper()
        if top and top in thesis_tickers:
            note += f" Thesis-relevant ({top})."
        candidates.append(BasketActionCandidate(
            basket=row.get("basket", ""),
            weight_pct=row.get("weight_pct", 0.0),
            band_min_pct=row.get("band_min_pct"),
            band_max_pct=row.get("band_max_pct"),
            band_status=status,
            verdict=verdict,
            note=note,
        ))
    return candidates
