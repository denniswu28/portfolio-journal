"""Tests for the QuantLib-backed option pricing, greeks, and implied vol."""

import math
from datetime import date

import pytest

from src.options.pricing import (
    greeks,
    implied_volatility,
    price_option,
    time_to_expiry,
)

EVAL = date(2026, 1, 1)
EXP = date(2027, 1, 1)  # exactly 1 year (Actual/365 -> 1.0)


class TestEuropeanPricing:
    def test_call_matches_black_scholes(self):
        c = price_option(100, 100, EXP, 0.05, 0.20, "CALL", eval_date=EVAL, american=False)
        assert c == pytest.approx(10.4506, abs=0.01)

    def test_put_matches_black_scholes(self):
        p = price_option(100, 100, EXP, 0.05, 0.20, "PUT", eval_date=EVAL, american=False)
        assert p == pytest.approx(5.5735, abs=0.01)

    def test_put_call_parity(self):
        c = price_option(100, 100, EXP, 0.05, 0.20, "CALL", eval_date=EVAL, american=False)
        p = price_option(100, 100, EXP, 0.05, 0.20, "PUT", eval_date=EVAL, american=False)
        # C - P = S - K e^{-rT}
        assert (c - p) == pytest.approx(100 - 100 * math.exp(-0.05 * 1.0), abs=0.02)


class TestAmericanPricing:
    def test_american_put_at_least_european(self):
        euro = price_option(100, 100, EXP, 0.05, 0.20, "PUT", eval_date=EVAL, american=False)
        amer = price_option(100, 100, EXP, 0.05, 0.20, "PUT", eval_date=EVAL, american=True)
        assert amer >= euro - 1e-9

    def test_expired_option_is_intrinsic(self):
        assert price_option(120, 100, EVAL, 0.05, 0.2, "CALL", eval_date=EVAL) == pytest.approx(20.0)
        assert price_option(80, 100, EVAL, 0.05, 0.2, "PUT", eval_date=EVAL) == pytest.approx(20.0)


class TestGreeks:
    def test_call_greeks_signs_and_delta(self):
        g = greeks(100, 100, EXP, 0.05, 0.20, "CALL", eval_date=EVAL, american=False)
        assert g["delta"] == pytest.approx(0.6368, abs=0.01)
        assert g["gamma"] > 0
        assert g["vega"] > 0
        assert g["theta"] < 0  # long option bleeds time value

    def test_put_delta_negative(self):
        g = greeks(100, 100, EXP, 0.05, 0.20, "PUT", eval_date=EVAL, american=False)
        assert -1.0 < g["delta"] < 0.0


class TestImpliedVol:
    def test_recovers_input_vol(self):
        price = price_option(100, 100, EXP, 0.05, 0.20, "CALL", eval_date=EVAL, american=False)
        iv = implied_volatility(price, 100, 100, EXP, 0.05, "CALL", eval_date=EVAL, american=False)
        assert iv == pytest.approx(0.20, abs=1e-3)

    def test_below_intrinsic_returns_none(self):
        iv = implied_volatility(1.0, 130, 100, EXP, 0.05, "CALL", eval_date=EVAL)
        assert iv is None


def test_time_to_expiry_year_fraction():
    assert time_to_expiry(EXP, EVAL) == pytest.approx(1.0, abs=1e-6)
    assert time_to_expiry(EVAL, EXP) == 0.0  # past expiry clamps to 0
