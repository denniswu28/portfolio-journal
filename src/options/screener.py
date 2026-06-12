"""
screener.py - Deterministic option screener: which strike to buy or which put to sell.

Given a directional/income thesis and a live option chain, this ranks candidate
defined-risk structures by probability of profit, expected value, return on margin,
and reward/risk. It translates the weekly thesis (e.g. the boist "sell puts on
SNDK/MU" or "buy lotto calls") into concrete, fully-specified candidate orders.

All pricing/greeks/POP/EV are deterministic (QuantLib + lognormal density). Market
mid premiums from the chain are reported alongside the model for confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd

from src.data_ingestion.market_data import OptionChain
from src.options.models import OptionStrategy
from src.options.pricing import time_to_expiry
from src.options.strategies import (
    StrategyAnalysis,
    analyze_strategy,
    bull_call_spread,
    bull_put_spread,
    cash_secured_put,
    expected_payoff,
    long_call,
    validate_level2,
)


@dataclass
class ScreenCandidate:
    """A ranked, fully-analyzed candidate structure."""

    strategy: OptionStrategy
    analysis: StrategyAnalysis
    score: float
    pop: Optional[float]
    expected_value: Optional[float]
    return_on_margin: float          # premium credit (or -debit) / buying power
    annualized_ror: float            # return_on_margin annualized over DTE
    distance_pct: float              # short/long strike distance from spot (%)
    market_mid: Optional[float] = None   # market mid premium per share (reference)
    components: dict = field(default_factory=dict)

    @property
    def short_strike(self) -> Optional[float]:
        shorts = [l.strike for l in self.strategy.legs if l.action == "SELL"]
        return max(shorts) if shorts else None


def _iv_from_chain(chain: OptionChain, right: str, strike: float) -> Optional[float]:
    df = chain.side(right)
    if df is None or df.empty or "impliedVolatility" not in df.columns:
        return None
    idx = (df["strike"] - strike).abs().idxmin()
    iv = float(df.loc[idx, "impliedVolatility"])
    return iv if iv > 0 else None


def _mid_from_chain(chain: OptionChain, right: str, strike: float) -> Optional[float]:
    df = chain.side(right)
    if df is None or df.empty or "strike" not in df.columns:
        return None
    idx = (df["strike"] - strike).abs().idxmin()
    bid = float(df.loc[idx].get("bid", 0.0) or 0.0)
    ask = float(df.loc[idx].get("ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    last = float(df.loc[idx].get("lastPrice", 0.0) or 0.0)
    return last if last > 0 else None


def _otm_put_strikes(chain: OptionChain, spot: float, min_otm: float, max_otm: float) -> List[float]:
    df = chain.puts
    if df is None or df.empty or "strike" not in df.columns:
        return []
    lo, hi = spot * (1 - max_otm), spot * (1 - min_otm)
    strikes = sorted({float(s) for s in df["strike"] if lo <= float(s) <= hi})
    return strikes


def _otm_call_strikes(chain: OptionChain, spot: float, min_otm: float, max_otm: float) -> List[float]:
    df = chain.calls
    if df is None or df.empty or "strike" not in df.columns:
        return []
    lo, hi = spot * (1 + min_otm), spot * (1 + max_otm)
    strikes = sorted({float(s) for s in df["strike"] if lo <= float(s) <= hi})
    return strikes


def _normalize(values: List[float]) -> List[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def rank_income_puts(
    chain: OptionChain,
    rate: float,
    spread_width: Optional[float] = None,
    min_otm: float = 0.03,
    max_otm: float = 0.20,
    contracts: int = 1,
    top: int = 5,
    eval_date: Optional[date] = None,
) -> List[ScreenCandidate]:
    """
    Rank put-selling candidates (cash-secured put, or bull put spread if width given).

    Answers "which put should I sell?": enumerates OTM short strikes, builds the
    defined-risk structure, and scores by POP, annualized return on margin, and EV.
    """
    spot = chain.spot
    if not spot:
        return []
    eval_date = eval_date or date.today()
    expiry = date.fromisoformat(chain.expiry)
    dte = max((expiry - eval_date).days, 1)

    raw: List[ScreenCandidate] = []
    for short_k in _otm_put_strikes(chain, spot, min_otm, max_otm):
        if spread_width:
            long_k = short_k - spread_width
            strategy = bull_put_spread(chain.ticker, short_k, long_k, expiry, contracts)
            vols = {short_k: _iv_from_chain(chain, "PUT", short_k) or 0.0,
                    long_k: _iv_from_chain(chain, "PUT", long_k) or 0.0}
            if not all(vols.values()):
                continue
        else:
            strategy = cash_secured_put(chain.ticker, short_k, expiry, contracts)
            iv = _iv_from_chain(chain, "PUT", short_k)
            if not iv:
                continue
            vols = {short_k: iv}
        if validate_level2(strategy):
            continue

        analysis = analyze_strategy(strategy, spot, rate, vols, eval_date=eval_date)
        credit = -analysis.net_debit  # income structures collect credit (>0)
        if credit <= 0:
            continue
        bpr = abs(analysis.max_loss) or 1.0
        rom = credit / bpr
        ann = rom * (365.0 / dte)
        t = time_to_expiry(expiry, eval_date)
        ev = expected_payoff(
            np.asarray(analysis.spot_grid),
            np.asarray(analysis.payoff),
            spot, vols[short_k], t, rate,
        )
        raw.append(ScreenCandidate(
            strategy=strategy, analysis=analysis, score=0.0,
            pop=analysis.pop, expected_value=ev,
            return_on_margin=rom, annualized_ror=ann,
            distance_pct=(spot - short_k) / spot * 100,
            market_mid=_mid_from_chain(chain, "PUT", short_k),
        ))

    return _score_and_sort(raw, top)


def rank_long_calls(
    chain: OptionChain,
    rate: float,
    min_otm: float = 0.05,
    max_otm: float = 0.40,
    contracts: int = 1,
    top: int = 5,
    eval_date: Optional[date] = None,
) -> List[ScreenCandidate]:
    """Rank long-call ("lotto") candidates by expected value per dollar of premium."""
    spot = chain.spot
    if not spot:
        return []
    eval_date = eval_date or date.today()
    expiry = date.fromisoformat(chain.expiry)
    dte = max((expiry - eval_date).days, 1)

    raw: List[ScreenCandidate] = []
    for strike in _otm_call_strikes(chain, spot, min_otm, max_otm):
        iv = _iv_from_chain(chain, "CALL", strike)
        if not iv:
            continue
        strategy = long_call(chain.ticker, strike, expiry, contracts)
        analysis = analyze_strategy(strategy, spot, rate, {strike: iv}, eval_date=eval_date)
        debit = analysis.net_debit
        if debit <= 0:
            continue
        t = time_to_expiry(expiry, eval_date)
        ev = expected_payoff(
            np.asarray(analysis.spot_grid),
            np.asarray(analysis.payoff),
            spot, iv, t, rate,
        )
        ev_per_dollar = (ev / debit) if (ev is not None and debit) else 0.0
        raw.append(ScreenCandidate(
            strategy=strategy, analysis=analysis, score=0.0,
            pop=analysis.pop, expected_value=ev,
            return_on_margin=ev_per_dollar, annualized_ror=ev_per_dollar * (365.0 / dte),
            distance_pct=(strike - spot) / spot * 100,
            market_mid=_mid_from_chain(chain, "CALL", strike),
        ))

    return _score_and_sort(raw, top, income=False)


def rank_call_debit_spreads(
    chain: OptionChain,
    rate: float,
    spread_width: float,
    min_otm: float = 0.0,
    max_otm: float = 0.15,
    contracts: int = 1,
    top: int = 5,
    eval_date: Optional[date] = None,
) -> List[ScreenCandidate]:
    """Rank bullish call debit spreads by reward/risk and POP."""
    spot = chain.spot
    if not spot:
        return []
    eval_date = eval_date or date.today()
    expiry = date.fromisoformat(chain.expiry)

    raw: List[ScreenCandidate] = []
    for long_k in _otm_call_strikes(chain, spot, min_otm, max_otm):
        short_k = long_k + spread_width
        v_long = _iv_from_chain(chain, "CALL", long_k)
        v_short = _iv_from_chain(chain, "CALL", short_k)
        if not v_long or not v_short:
            continue
        strategy = bull_call_spread(chain.ticker, long_k, short_k, expiry, contracts)
        if validate_level2(strategy):
            continue
        analysis = analyze_strategy(strategy, spot, rate, {long_k: v_long, short_k: v_short}, eval_date=eval_date)
        debit = analysis.net_debit
        if debit <= 0:
            continue
        rom = analysis.max_profit / abs(analysis.max_loss) if analysis.max_loss else 0.0
        raw.append(ScreenCandidate(
            strategy=strategy, analysis=analysis, score=0.0,
            pop=analysis.pop, expected_value=None,
            return_on_margin=rom, annualized_ror=rom,
            distance_pct=(long_k - spot) / spot * 100,
            market_mid=None,
        ))

    return _score_and_sort(raw, top)


def _score_and_sort(candidates: List[ScreenCandidate], top: int, income: bool = True) -> List[ScreenCandidate]:
    if not candidates:
        return []
    pops = [c.pop or 0.0 for c in candidates]
    anns = _normalize([c.annualized_ror for c in candidates])
    evs = _normalize([(c.expected_value or 0.0) for c in candidates])
    for c, ann_n, ev_n in zip(candidates, anns, evs):
        if income:
            # Income: favor high POP and return on margin, with EV as a tie-breaker.
            c.score = 0.40 * (c.pop or 0.0) + 0.40 * ann_n + 0.20 * ev_n
            c.components = {"pop": round(c.pop or 0.0, 4), "ann_ror_norm": round(ann_n, 4), "ev_norm": round(ev_n, 4)}
        else:
            # Convexity bets: favor EV per dollar over raw POP.
            c.score = 0.60 * ev_n + 0.25 * ann_n + 0.15 * (c.pop or 0.0)
            c.components = {"ev_norm": round(ev_n, 4), "ann_ror_norm": round(ann_n, 4), "pop": round(c.pop or 0.0, 4)}
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:top]


def screen_chain(
    chain: OptionChain,
    direction: str,
    rate: float,
    spread_width: Optional[float] = None,
    contracts: int = 1,
    top: int = 5,
    eval_date: Optional[date] = None,
) -> List[ScreenCandidate]:
    """
    Dispatch a screen by thesis direction:
      * "income" / "bullish-income" -> sell puts (CSP or bull put spread).
      * "bullish"                    -> long calls (and call debit spreads if width).
      * "bearish"                    -> (reserved) bear structures.
    """
    token = direction.strip().lower()
    if token in ("income", "bullish-income", "put-selling"):
        return rank_income_puts(chain, rate, spread_width=spread_width, contracts=contracts, top=top, eval_date=eval_date)
    if token in ("bullish", "long", "calls"):
        if spread_width:
            return rank_call_debit_spreads(chain, rate, spread_width, contracts=contracts, top=top, eval_date=eval_date)
        return rank_long_calls(chain, rate, contracts=contracts, top=top, eval_date=eval_date)
    raise ValueError(f"Unsupported screen direction '{direction}'. Use income, bullish.")
