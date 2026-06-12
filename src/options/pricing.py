"""
pricing.py - Deterministic option pricing and greeks via QuantLib + scipy.

Pricing uses QuantLib's Black-Scholes-Merton process:
  * European options  -> AnalyticEuropeanEngine (closed form).
  * American options  -> BaroneAdesiWhaleyApproximationEngine (early-exercise aware).

Equity and ETF options are American by default (they can be exercised early and pay
dividends); index options should pass ``american=False``.

Greeks are computed by central finite differences around ``price_option`` so that the
full set (delta, gamma, theta, vega, rho) is available uniformly for both exercise
styles, independent of engine-specific greek support. Implied volatility is solved
with scipy's Brent method.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Union

import QuantLib as ql
from scipy.optimize import brentq

from src.options.models import CALL, PUT

DateLike = Union[date, str]

# Day-count used consistently for term structures and year fractions.
_DAY_COUNT = ql.Actual365Fixed()


def _to_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _ql_date(d: date) -> ql.Date:
    return ql.Date(d.day, d.month, d.year)


def _normalize_right(right: str) -> int:
    token = right.upper().strip()
    if token in ("C", "CALL"):
        return ql.Option.Call
    if token in ("P", "PUT"):
        return ql.Option.Put
    raise ValueError(f"right must be CALL or PUT, got '{right}'")


def time_to_expiry(expiry: DateLike, eval_date: Optional[DateLike] = None) -> float:
    """Year fraction (Actual/365 Fixed) between eval date and expiry; >= 0."""
    exp = _to_date(expiry)
    ev = _to_date(eval_date) if eval_date is not None else date.today()
    return max(0.0, _DAY_COUNT.yearFraction(_ql_date(ev), _ql_date(exp)))


def _intrinsic(spot: float, strike: float, right: str) -> float:
    if _normalize_right(right) == ql.Option.Call:
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def price_option(
    spot: float,
    strike: float,
    expiry: DateLike,
    rate: float,
    vol: float,
    right: str,
    *,
    div_yield: float = 0.0,
    eval_date: Optional[DateLike] = None,
    american: bool = True,
) -> float:
    """
    Price a single option (per share) with QuantLib.

    Args:
        spot: Underlying price.
        strike: Option strike.
        expiry: Expiry date (date or ISO string).
        rate: Annualized risk-free rate (decimal).
        vol: Annualized volatility (decimal).
        right: "CALL" or "PUT".
        div_yield: Continuous dividend yield (decimal).
        eval_date: Valuation date (defaults to today).
        american: True for American exercise (equity/ETF), False for European.

    Returns:
        Theoretical option value per share (multiply by 100 * contracts for a position).
    """
    exp = _to_date(expiry)
    ev = _to_date(eval_date) if eval_date is not None else date.today()
    option_type = _normalize_right(right)

    if exp <= ev:
        return _intrinsic(spot, strike, right)

    vol = max(float(vol), 1e-6)
    spot = max(float(spot), 1e-9)

    ql_eval = _ql_date(ev)
    ql.Settings.instance().evaluationDate = ql_eval
    ql_exp = _ql_date(exp)

    spot_handle = ql.QuoteHandle(ql.SimpleQuote(spot))
    rate_ts = ql.YieldTermStructureHandle(ql.FlatForward(ql_eval, rate, _DAY_COUNT))
    div_ts = ql.YieldTermStructureHandle(ql.FlatForward(ql_eval, div_yield, _DAY_COUNT))
    vol_ts = ql.BlackVolTermStructureHandle(
        ql.BlackConstantVol(ql_eval, ql.NullCalendar(), vol, _DAY_COUNT)
    )
    process = ql.BlackScholesMertonProcess(spot_handle, div_ts, rate_ts, vol_ts)
    payoff = ql.PlainVanillaPayoff(option_type, strike)

    if american:
        exercise = ql.AmericanExercise(ql_eval, ql_exp)
        option = ql.VanillaOption(payoff, exercise)
        option.setPricingEngine(ql.BaroneAdesiWhaleyApproximationEngine(process))
    else:
        exercise = ql.EuropeanExercise(ql_exp)
        option = ql.VanillaOption(payoff, exercise)
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))

    return float(option.NPV())


def greeks(
    spot: float,
    strike: float,
    expiry: DateLike,
    rate: float,
    vol: float,
    right: str,
    *,
    div_yield: float = 0.0,
    eval_date: Optional[DateLike] = None,
    american: bool = True,
) -> Dict[str, float]:
    """
    Per-share price and greeks via central finite differences.

    Conventions:
        delta  - per $1 move in spot
        gamma  - change in delta per $1 move in spot
        vega   - per 1 volatility point (1%), i.e. dP/dvol * 0.01
        theta  - per calendar day (typically negative for long options)
        rho    - per 1 percentage point (1%) move in rate, i.e. dP/dr * 0.01
    """
    ev = _to_date(eval_date) if eval_date is not None else date.today()

    def price_with(s: float = spot, v: float = vol, r: float = rate, ed: date = ev) -> float:
        return price_option(
            s, strike, expiry, r, v, right,
            div_yield=div_yield, eval_date=ed, american=american,
        )

    base = price_with()

    h = max(0.01 * spot, 0.01)
    p_up = price_with(s=spot + h)
    p_dn = price_with(s=spot - h)
    delta = (p_up - p_dn) / (2 * h)
    gamma = (p_up - 2 * base + p_dn) / (h * h)

    dv = 0.01
    vega = (price_with(v=vol + dv) - price_with(v=max(vol - dv, 1e-6))) / (2 * dv) * 0.01

    dr = 1e-4
    rho = (price_with(r=rate + dr) - price_with(r=rate - dr)) / (2 * dr) * 0.01

    # Theta: advance the valuation date by one calendar day.
    next_day = ev + timedelta(days=1)
    theta = price_with(ed=next_day) - base

    return {
        "price": base,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
    }


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    expiry: DateLike,
    rate: float,
    right: str,
    *,
    div_yield: float = 0.0,
    eval_date: Optional[DateLike] = None,
    american: bool = True,
    low: float = 1e-4,
    high: float = 5.0,
) -> Optional[float]:
    """
    Solve for the Black-Scholes-Merton implied volatility (decimal) via Brent.

    Returns None if the market price is below intrinsic or no root is bracketed.
    """
    intrinsic = _intrinsic(spot, strike, right)
    if market_price < intrinsic - 1e-6:
        return None

    def objective(v: float) -> float:
        return price_option(
            spot, strike, expiry, rate, v, right,
            div_yield=div_yield, eval_date=eval_date, american=american,
        ) - market_price

    try:
        f_low, f_high = objective(low), objective(high)
        if f_low * f_high > 0:
            return None
        return float(brentq(objective, low, high, maxiter=100, xtol=1e-6))
    except (ValueError, RuntimeError):
        return None
