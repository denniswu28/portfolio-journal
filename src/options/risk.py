"""
risk.py - Portfolio-level option risk: net greeks, stress scenarios, sleeve sizing.

Aggregates greeks across open option strategies (and their underlying share deltas),
runs deterministic spot/IV stress scenarios, and checks the options sleeve against its
~10% target. Used by the daily monitor and the risk prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from src.options.models import OptionStrategy
from src.options.strategies import mark_strategy, net_greeks

DEFAULT_SPOT_SHOCKS = (-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15)
DEFAULT_VOL_SHOCKS = (0.0,)


@dataclass
class PortfolioOptionRisk:
    """Aggregated option-book risk at current marks."""

    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0          # dollars/day
    net_vega: float = 0.0           # dollars/vol-pt
    net_rho: float = 0.0            # dollars/1%
    dollar_delta: float = 0.0       # delta * spot, summed (directional $ exposure)
    gross_market_value: float = 0.0
    by_underlying: Dict[str, dict] = field(default_factory=dict)


def aggregate_greeks(
    strategies: Sequence[OptionStrategy],
    spot_map: Dict[str, float],
    rate: float,
    vol_map: Dict[str, float],
    eval_date: Optional[date] = None,
    american: bool = True,
) -> PortfolioOptionRisk:
    """Sum net greeks, dollar-delta, and mark value across all option strategies."""
    risk = PortfolioOptionRisk()
    for strat in strategies:
        u = strat.underlying
        spot = spot_map.get(u)
        vol = vol_map.get(u)
        if spot is None or vol is None:
            continue
        g = net_greeks(strat, spot, rate, vol, eval_date=eval_date, american=american)
        mark = mark_strategy(strat, spot, rate, vol, eval_date=eval_date, american=american)
        dollar_delta = g["delta"] * spot
        risk.net_delta += g["delta"]
        risk.net_gamma += g["gamma"]
        risk.net_theta += g["theta"]
        risk.net_vega += g["vega"]
        risk.net_rho += g["rho"]
        risk.dollar_delta += dollar_delta
        risk.gross_market_value += abs(mark)
        bucket = risk.by_underlying.setdefault(
            u, {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0, "dollar_delta": 0.0, "mark": 0.0}
        )
        bucket["delta"] += g["delta"]
        bucket["gamma"] += g["gamma"]
        bucket["theta"] += g["theta"]
        bucket["vega"] += g["vega"]
        bucket["rho"] += g["rho"]
        bucket["dollar_delta"] += dollar_delta
        bucket["mark"] += mark
    return risk


def stress_test(
    strategies: Sequence[OptionStrategy],
    spot_map: Dict[str, float],
    rate: float,
    vol_map: Dict[str, float],
    spot_shocks: Sequence[float] = DEFAULT_SPOT_SHOCKS,
    vol_shocks: Sequence[float] = DEFAULT_VOL_SHOCKS,
    eval_date: Optional[date] = None,
    american: bool = True,
) -> List[dict]:
    """
    Re-mark the whole option book under spot and IV shocks; report change in value.

    Each row's ``pnl`` is the change in aggregate mark-to-model value versus the base
    (unshocked) marks, isolating the book's sensitivity to the scenario.
    """
    base = 0.0
    for strat in strategies:
        u = strat.underlying
        if u in spot_map and u in vol_map:
            base += mark_strategy(strat, spot_map[u], rate, vol_map[u], eval_date=eval_date, american=american)

    rows: List[dict] = []
    for sp in spot_shocks:
        for vs in vol_shocks:
            shocked = 0.0
            for strat in strategies:
                u = strat.underlying
                if u not in spot_map or u not in vol_map:
                    continue
                s = spot_map[u] * (1 + sp)
                v = max(vol_map[u] * (1 + vs), 1e-6)
                shocked += mark_strategy(strat, s, rate, v, eval_date=eval_date, american=american)
            rows.append({
                "spot_shock_pct": round(sp * 100, 1),
                "vol_shock_pct": round(vs * 100, 1),
                "pnl": round(shocked - base, 2),
            })
    return rows


def options_sleeve_status(
    options_value: float,
    portfolio_value: float,
    target_pct: float = 10.0,
    min_pct: float = 0.0,
    max_pct: float = 15.0,
) -> dict:
    """Check the options sleeve weight against its target band."""
    weight = (options_value / portfolio_value * 100) if portfolio_value else 0.0
    if weight > max_pct:
        status = "ABOVE"
    elif weight < min_pct:
        status = "BELOW"
    else:
        status = "OK"
    return {
        "options_value": round(options_value, 2),
        "portfolio_value": round(portfolio_value, 2),
        "weight_pct": round(weight, 2),
        "target_pct": target_pct,
        "status": status,
    }
