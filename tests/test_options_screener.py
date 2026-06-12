"""Tests for the deterministic option screener (synthetic chains, no network)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from src.data_ingestion.market_data import OptionChain
from src.options.screener import (
    rank_call_debit_spreads,
    rank_income_puts,
    rank_long_calls,
    screen_chain,
)
from src.options.strategies import validate_level2


def _chain(spot=632.0, dte=45):
    expiry = (date.today() + timedelta(days=dte)).isoformat()
    put_strikes = list(range(500, 640, 10))
    calls_strikes = list(range(630, 780, 10))
    puts = pd.DataFrame({
        "strike": put_strikes,
        "impliedVolatility": [0.40 - 0.0005 * (k - 500) for k in put_strikes],
        "bid": [max(0.5, (632 - k) * 0.02 + 3) for k in put_strikes],
        "ask": [max(0.7, (632 - k) * 0.02 + 3.4) for k in put_strikes],
        "lastPrice": [max(0.6, (632 - k) * 0.02 + 3.2) for k in put_strikes],
        "openInterest": [250] * len(put_strikes),
    })
    calls = pd.DataFrame({
        "strike": calls_strikes,
        "impliedVolatility": [0.32 + 0.0004 * (k - 630) for k in calls_strikes],
        "bid": [max(0.5, (632 - k) * 0.0 + 4) for k in calls_strikes],
        "ask": [max(0.7, 4.4) for _ in calls_strikes],
        "lastPrice": [max(0.6, 4.2) for _ in calls_strikes],
        "openInterest": [180] * len(calls_strikes),
    })
    return OptionChain(ticker="SMH", expiry=expiry, spot=spot, calls=calls, puts=puts)


class TestRankIncomePuts:
    def test_returns_ranked_cash_secured_puts(self):
        cands = rank_income_puts(_chain(), rate=0.043, top=5)
        assert cands
        assert all(c.strategy.name == "cash-secured put" for c in cands)
        # Level-2 compliant and collecting credit.
        assert all(validate_level2(c.strategy) == [] for c in cands)
        assert all(c.analysis.net_debit < 0 for c in cands)
        # Sorted by score descending.
        scores = [c.score for c in cands]
        assert scores == sorted(scores, reverse=True)

    def test_short_strikes_are_otm(self):
        spot = 632.0
        cands = rank_income_puts(_chain(spot=spot), rate=0.043, top=8)
        assert all(c.short_strike < spot for c in cands)

    def test_spread_width_builds_defined_risk_put_spreads(self):
        cands = rank_income_puts(_chain(), rate=0.043, spread_width=20.0, top=5)
        assert cands
        assert all(c.strategy.name == "bull put spread" for c in cands)
        # Defined risk: max loss bounded well above a naked put's.
        assert all(c.analysis.max_loss > -3000 for c in cands)


class TestRankLongCalls:
    def test_returns_long_calls_with_debit(self):
        cands = rank_long_calls(_chain(), rate=0.043, top=5)
        assert cands
        assert all(c.strategy.name == "long call" for c in cands)
        assert all(c.analysis.net_debit > 0 for c in cands)


class TestRankCallDebitSpreads:
    def test_defined_risk_call_spreads(self):
        cands = rank_call_debit_spreads(_chain(), rate=0.043, spread_width=20.0, top=5)
        assert cands
        assert all(validate_level2(c.strategy) == [] for c in cands)
        assert all(c.analysis.net_debit > 0 for c in cands)


class TestScreenDispatcher:
    def test_income_dispatch(self):
        assert screen_chain(_chain(), "income", 0.043, top=3)

    def test_bullish_dispatch(self):
        assert screen_chain(_chain(), "bullish", 0.043, top=3)

    def test_unknown_direction_raises(self):
        with pytest.raises(ValueError):
            screen_chain(_chain(), "sideways", 0.043)
