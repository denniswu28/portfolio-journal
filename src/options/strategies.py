"""
strategies.py - Level-2 option strategies, payoff/greeks math, and compliance.

Builds the permitted Level-2 structures as ``OptionStrategy`` objects, computes
expiry payoff, breakevens, max profit/loss, net greeks, probability of profit, and a
mark-to-model P&L at a future date. ``validate_level2`` rejects naked calls and any
short leg whose risk is not defined by a long leg or held stock/cash.

All dollar figures are position-level (per-share value * multiplier * contracts).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Union

import numpy as np

from src.options.models import (
    BUY,
    CALL,
    PUT,
    SECURED_CASH,
    SECURED_SHORT_STOCK,
    SECURED_STOCK,
    SELL,
    OptionLeg,
    OptionStrategy,
)
from src.options.pricing import greeks as leg_greeks
from src.options.pricing import price_option, time_to_expiry

VolInput = Union[float, Dict[float, float]]


# ── STRATEGY METRICS ─────────────────────────────────────────────────────────

@dataclass
class StrategyAnalysis:
    """Deterministic analytics bundle for one strategy at given market params."""

    net_debit: float          # position dollars; > 0 debit paid, < 0 credit received
    max_profit: float
    max_loss: float
    breakevens: List[float]
    pop: Optional[float]      # probability of profit at expiry (0-1)
    net_delta: float
    net_gamma: float
    net_theta: float          # per day
    net_vega: float           # per 1 vol point
    net_rho: float            # per 1% rate
    leg_prices: List[float] = field(default_factory=list)
    spot_grid: List[float] = field(default_factory=list)
    payoff: List[float] = field(default_factory=list)

    @property
    def is_credit(self) -> bool:
        return self.net_debit < 0

    @property
    def risk_reward(self) -> Optional[float]:
        if self.max_loss == 0:
            return None
        return abs(self.max_profit / self.max_loss)


# ── STRATEGY BUILDERS ────────────────────────────────────────────────────────

def _leg(underlying, right, strike, expiry, action, contracts=1, entry_price=None) -> OptionLeg:
    return OptionLeg(
        underlying=underlying, right=right, strike=strike, expiry=expiry,
        action=action, contracts=contracts, entry_price=entry_price,
    )


def long_call(underlying, strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="long call", underlying=underlying,
                          legs=[_leg(underlying, CALL, strike, expiry, BUY, contracts)])


def long_put(underlying, strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="long put", underlying=underlying,
                          legs=[_leg(underlying, PUT, strike, expiry, BUY, contracts)])


def bull_call_spread(underlying, long_strike, short_strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="bull call spread", underlying=underlying, legs=[
        _leg(underlying, CALL, long_strike, expiry, BUY, contracts),
        _leg(underlying, CALL, short_strike, expiry, SELL, contracts),
    ])


def bear_put_spread(underlying, long_strike, short_strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="bear put spread", underlying=underlying, legs=[
        _leg(underlying, PUT, long_strike, expiry, BUY, contracts),
        _leg(underlying, PUT, short_strike, expiry, SELL, contracts),
    ])


def bull_put_spread(underlying, short_strike, long_strike, expiry, contracts=1) -> OptionStrategy:
    """Credit spread: sell higher put, buy lower put (defined risk)."""
    return OptionStrategy(name="bull put spread", underlying=underlying, legs=[
        _leg(underlying, PUT, short_strike, expiry, SELL, contracts),
        _leg(underlying, PUT, long_strike, expiry, BUY, contracts),
    ])


def bear_call_spread(underlying, short_strike, long_strike, expiry, contracts=1) -> OptionStrategy:
    """Credit spread: sell lower call, buy higher call (defined risk)."""
    return OptionStrategy(name="bear call spread", underlying=underlying, legs=[
        _leg(underlying, CALL, short_strike, expiry, SELL, contracts),
        _leg(underlying, CALL, long_strike, expiry, BUY, contracts),
    ])


def cash_secured_put(underlying, strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="cash-secured put", underlying=underlying,
                          secured_by=SECURED_CASH,
                          legs=[_leg(underlying, PUT, strike, expiry, SELL, contracts)])


def covered_call(underlying, strike, expiry, contracts=1, shares=None) -> OptionStrategy:
    shares = shares if shares is not None else contracts * 100
    return OptionStrategy(name="covered call", underlying=underlying,
                          secured_by=SECURED_STOCK, underlying_shares=shares,
                          legs=[_leg(underlying, CALL, strike, expiry, SELL, contracts)])


def covered_put(underlying, strike, expiry, contracts=1, short_shares=None) -> OptionStrategy:
    short_shares = short_shares if short_shares is not None else contracts * 100
    return OptionStrategy(name="covered put", underlying=underlying,
                          secured_by=SECURED_SHORT_STOCK, underlying_shares=-abs(short_shares),
                          legs=[_leg(underlying, PUT, strike, expiry, SELL, contracts)])


def long_straddle(underlying, strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="long straddle", underlying=underlying, legs=[
        _leg(underlying, CALL, strike, expiry, BUY, contracts),
        _leg(underlying, PUT, strike, expiry, BUY, contracts),
    ])


def long_strangle(underlying, put_strike, call_strike, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="long strangle", underlying=underlying, legs=[
        _leg(underlying, PUT, put_strike, expiry, BUY, contracts),
        _leg(underlying, CALL, call_strike, expiry, BUY, contracts),
    ])


def iron_condor(underlying, put_long, put_short, call_short, call_long, expiry, contracts=1) -> OptionStrategy:
    return OptionStrategy(name="iron condor", underlying=underlying, legs=[
        _leg(underlying, PUT, put_long, expiry, BUY, contracts),
        _leg(underlying, PUT, put_short, expiry, SELL, contracts),
        _leg(underlying, CALL, call_short, expiry, SELL, contracts),
        _leg(underlying, CALL, call_long, expiry, BUY, contracts),
    ])


# Strike-order contract for build_named_strategy (documented for the CLI/screener).
NAMED_STRUCTURES = {
    "long-call": 1,
    "long-put": 1,
    "bull-call-spread": 2,   # [long lower, short higher]
    "bear-put-spread": 2,    # [long higher, short lower]
    "bull-put-spread": 2,    # [short higher, long lower]
    "bear-call-spread": 2,   # [short lower, long higher]
    "cash-secured-put": 1,
    "covered-call": 1,
    "long-straddle": 1,
    "long-strangle": 2,      # [put, call]
    "iron-condor": 4,        # [put long, put short, call short, call long]
}


def build_named_strategy(
    name: str,
    underlying: str,
    expiry,
    strikes: Sequence[float],
    contracts: int = 1,
    shares: Optional[float] = None,
) -> OptionStrategy:
    """Construct a known structure from a normalized name and ordered strikes."""
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    expected = NAMED_STRUCTURES.get(key)
    if expected is None:
        raise ValueError(f"Unknown structure '{name}'. Known: {', '.join(sorted(NAMED_STRUCTURES))}")
    if len(strikes) != expected:
        raise ValueError(f"{key} needs {expected} strike(s), got {len(strikes)}: {list(strikes)}")
    k = [float(s) for s in strikes]
    if key == "long-call":
        return long_call(underlying, k[0], expiry, contracts)
    if key == "long-put":
        return long_put(underlying, k[0], expiry, contracts)
    if key == "bull-call-spread":
        return bull_call_spread(underlying, k[0], k[1], expiry, contracts)
    if key == "bear-put-spread":
        return bear_put_spread(underlying, k[0], k[1], expiry, contracts)
    if key == "bull-put-spread":
        return bull_put_spread(underlying, k[0], k[1], expiry, contracts)
    if key == "bear-call-spread":
        return bear_call_spread(underlying, k[0], k[1], expiry, contracts)
    if key == "cash-secured-put":
        return cash_secured_put(underlying, k[0], expiry, contracts)
    if key == "covered-call":
        return covered_call(underlying, k[0], expiry, contracts, shares=shares)
    if key == "long-straddle":
        return long_straddle(underlying, k[0], expiry, contracts)
    if key == "long-strangle":
        return long_strangle(underlying, k[0], k[1], expiry, contracts)
    if key == "iron-condor":
        return iron_condor(underlying, k[0], k[1], k[2], k[3], expiry, contracts)
    raise ValueError(f"Unhandled structure '{name}'")


# ── LEVEL-2 COMPLIANCE ───────────────────────────────────────────────────────

def validate_level2(strategy: OptionStrategy) -> List[str]:
    """
    Return a list of Level-2 compliance violations (empty list == compliant).

    Rejects: more than 4 legs; short calls not covered by long calls or long stock
    (naked calls); short puts not covered by long puts, cash, or short stock. A long
    option of matching size defines the risk of a short option of the same right in
    either vertical configuration (debit or credit), so coverage is assessed by
    aggregate contract counts rather than by strike ordering.
    """
    violations: List[str] = []
    legs = strategy.legs
    if len(legs) > 4:
        violations.append(f"Strategy has {len(legs)} legs; Level-2 spreads allow at most 4.")

    multiplier = legs[0].multiplier if legs else 100
    long_call_contracts = sum(l.contracts for l in legs if l.is_call() and l.action == BUY)
    short_call_contracts = sum(l.contracts for l in legs if l.is_call() and l.action == SELL)
    long_put_contracts = sum(l.contracts for l in legs if l.is_put() and l.action == BUY)
    short_put_contracts = sum(l.contracts for l in legs if l.is_put() and l.action == SELL)

    stock_cover_contracts = 0
    if strategy.secured_by == SECURED_STOCK and strategy.underlying_shares > 0:
        stock_cover_contracts = int(strategy.underlying_shares // multiplier)

    if short_call_contracts > long_call_contracts + stock_cover_contracts:
        violations.append(
            "Uncovered short CALL exposure: naked calls are forbidden at Level 2 "
            "(cover each short call with a long call or 100 shares of long stock)."
        )

    puts_secured = strategy.secured_by in (SECURED_CASH, SECURED_SHORT_STOCK)
    if short_put_contracts > long_put_contracts and not puts_secured:
        violations.append(
            "Unsecured short PUT exposure: secure with cash, a long put of equal size, "
            "or short stock."
        )

    return violations


# ── PAYOFF / GREEKS / P&L ────────────────────────────────────────────────────

def _vol_for(strike: float, vols: VolInput) -> float:
    if isinstance(vols, dict):
        if strike in vols:
            return vols[strike]
        # nearest available strike
        nearest = min(vols, key=lambda k: abs(k - strike))
        return vols[nearest]
    return float(vols)


def _leg_intrinsic(leg: OptionLeg, spot: float) -> float:
    if leg.is_call():
        return max(0.0, spot - leg.strike)
    return max(0.0, leg.strike - spot)


def _spot_grid(strategy: OptionStrategy, spot: float, points: int = 401) -> np.ndarray:
    strikes = [leg.strike for leg in strategy.legs]
    low = 0.4 * min([spot, *strikes])
    high = 1.8 * max([spot, *strikes])
    return np.linspace(max(low, 0.01), high, points)


def payoff_at_expiry(strategy: OptionStrategy, spots: Sequence[float], net_debit: float) -> np.ndarray:
    """Position P&L at expiry across ``spots`` given the net debit paid (credit < 0)."""
    spots = np.asarray(spots, dtype=float)
    value = np.zeros_like(spots)
    for leg in strategy.legs:
        qty = leg.contracts * leg.multiplier
        intrinsic = np.array([_leg_intrinsic(leg, s) for s in spots])
        value += leg.sign * intrinsic * qty
    return value - net_debit


def net_entry_debit(strategy: OptionStrategy, leg_prices: Sequence[float]) -> float:
    """Net cash outlay (position dollars): positive = debit paid, negative = credit."""
    total = 0.0
    for leg, price in zip(strategy.legs, leg_prices):
        total += leg.sign * price * leg.contracts * leg.multiplier
    return total


def _breakevens(spots: np.ndarray, payoff: np.ndarray) -> List[float]:
    result: List[float] = []
    for i in range(1, len(payoff)):
        y0, y1 = payoff[i - 1], payoff[i]
        if y0 == 0.0:
            result.append(float(spots[i - 1]))
        elif y0 * y1 < 0:
            x0, x1 = spots[i - 1], spots[i]
            result.append(float(x0 - y0 * (x1 - x0) / (y1 - y0)))
    return sorted(round(x, 2) for x in result)


def probability_of_profit(
    spots: np.ndarray,
    payoff: np.ndarray,
    spot: float,
    vol: float,
    t_years: float,
    rate: float,
    div_yield: float = 0.0,
) -> Optional[float]:
    """Risk-neutral lognormal probability that the expiry payoff is positive."""
    if t_years <= 0 or vol <= 0 or spot <= 0:
        return None
    spots = np.asarray(spots, dtype=float)
    mu = math.log(spot) + (rate - div_yield - 0.5 * vol * vol) * t_years
    sigma = vol * math.sqrt(t_years)
    with np.errstate(divide="ignore"):
        pdf = np.where(
            spots > 0,
            np.exp(-((np.log(spots) - mu) ** 2) / (2 * sigma * sigma)) / (spots * sigma * math.sqrt(2 * math.pi)),
            0.0,
        )
    mass = np.trapezoid(pdf, spots)
    if mass <= 0:
        return None
    profit_mass = np.trapezoid(np.where(payoff > 0, pdf, 0.0), spots)
    return float(max(0.0, min(1.0, profit_mass / mass)))


def expected_payoff(
    spots: np.ndarray,
    payoff: np.ndarray,
    spot: float,
    vol: float,
    t_years: float,
    rate: float,
    div_yield: float = 0.0,
) -> Optional[float]:
    """Risk-neutral expected payoff (dollars) using the lognormal terminal density."""
    if t_years <= 0 or vol <= 0 or spot <= 0:
        return None
    spots = np.asarray(spots, dtype=float)
    payoff = np.asarray(payoff, dtype=float)
    mu = math.log(spot) + (rate - div_yield - 0.5 * vol * vol) * t_years
    sigma = vol * math.sqrt(t_years)
    with np.errstate(divide="ignore"):
        pdf = np.where(
            spots > 0,
            np.exp(-((np.log(spots) - mu) ** 2) / (2 * sigma * sigma)) / (spots * sigma * math.sqrt(2 * math.pi)),
            0.0,
        )
    mass = np.trapezoid(pdf, spots)
    if mass <= 0:
        return None
    return float(np.trapezoid(payoff * pdf, spots) / mass)


def mark_strategy(
    strategy: OptionStrategy,
    spot: float,
    rate: float,
    vols: VolInput,
    eval_date: Optional[date] = None,
    american: bool = True,
    div_yield: float = 0.0,
) -> float:
    """Mark-to-model position value (dollars) at the given spot and date."""
    value = 0.0
    for leg in strategy.legs:
        price = price_option(
            spot, leg.strike, leg.expiry, rate, _vol_for(leg.strike, vols), leg.right,
            div_yield=div_yield, eval_date=eval_date, american=american,
        )
        value += leg.sign * price * leg.contracts * leg.multiplier
    return value


def net_greeks(
    strategy: OptionStrategy,
    spot: float,
    rate: float,
    vols: VolInput,
    eval_date: Optional[date] = None,
    american: bool = True,
    div_yield: float = 0.0,
) -> Dict[str, float]:
    """Position-level net greeks (delta, gamma, theta/day, vega/vol-pt, rho/1%)."""
    agg = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for leg in strategy.legs:
        g = leg_greeks(
            spot, leg.strike, leg.expiry, rate, _vol_for(leg.strike, vols), leg.right,
            div_yield=div_yield, eval_date=eval_date, american=american,
        )
        qty = leg.contracts * leg.multiplier
        for key in agg:
            agg[key] += leg.sign * g[key] * qty
    return agg


def analyze_strategy(
    strategy: OptionStrategy,
    spot: float,
    rate: float,
    vols: VolInput,
    eval_date: Optional[date] = None,
    american: bool = True,
    div_yield: float = 0.0,
) -> StrategyAnalysis:
    """
    Full deterministic analysis at current market parameters.

    Prices every leg, then derives net debit/credit, payoff curve, max profit/loss,
    breakevens, probability of profit, and position-level net greeks.
    """
    leg_prices: List[float] = []
    net_delta = net_gamma = net_theta = net_vega = net_rho = 0.0
    for leg in strategy.legs:
        vol = _vol_for(leg.strike, vols)
        g = leg_greeks(
            spot, leg.strike, leg.expiry, rate, vol, leg.right,
            div_yield=div_yield, eval_date=eval_date, american=american,
        )
        leg_prices.append(g["price"])
        qty = leg.contracts * leg.multiplier
        net_delta += leg.sign * g["delta"] * qty
        net_gamma += leg.sign * g["gamma"] * qty
        net_theta += leg.sign * g["theta"] * qty
        net_vega += leg.sign * g["vega"] * qty
        net_rho += leg.sign * g["rho"] * qty

    debit = net_entry_debit(strategy, leg_prices)
    grid = _spot_grid(strategy, spot)
    payoff = payoff_at_expiry(strategy, grid, debit)

    # Representative expiry/vol for POP (use the nearest expiry and its vol).
    nearest_expiry = min(leg.expiry for leg in strategy.legs)
    t_years = time_to_expiry(nearest_expiry, eval_date)
    pop_vol = _vol_for(spot, vols)
    pop = probability_of_profit(grid, payoff, spot, pop_vol, t_years, rate, div_yield)

    return StrategyAnalysis(
        net_debit=round(debit, 2),
        max_profit=round(float(np.max(payoff)), 2),
        max_loss=round(float(np.min(payoff)), 2),
        breakevens=_breakevens(grid, payoff),
        pop=pop,
        net_delta=round(net_delta, 4),
        net_gamma=round(net_gamma, 4),
        net_theta=round(net_theta, 2),
        net_vega=round(net_vega, 2),
        net_rho=round(net_rho, 2),
        leg_prices=[round(p, 4) for p in leg_prices],
        spot_grid=[round(float(s), 2) for s in grid],
        payoff=[round(float(p), 2) for p in payoff],
    )
