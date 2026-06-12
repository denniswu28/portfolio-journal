"""Tests for portfolio-level option risk aggregation and stress scenarios."""

from datetime import date, timedelta

import pytest

from src.options.risk import aggregate_greeks, options_sleeve_status, stress_test
from src.options.strategies import cash_secured_put, long_call

EXP = date.today() + timedelta(days=45)
SPOT = {"SMH": 632.0, "SNDK": 1800.0}
VOL = {"SMH": 0.30, "SNDK": 0.55}


def _book():
    return [
        long_call("SMH", 660, EXP, contracts=1),
        cash_secured_put("SNDK", 1500, EXP, contracts=1),
    ]


class TestAggregateGreeks:
    def test_net_delta_positive_for_long_call_and_short_put(self):
        risk = aggregate_greeks(_book(), SPOT, 0.043, VOL)
        assert risk.net_delta > 0          # both legs are long-delta
        assert risk.dollar_delta > 0
        assert risk.gross_market_value > 0
        assert set(risk.by_underlying) == {"SMH", "SNDK"}

    def test_skips_unknown_underlying(self):
        risk = aggregate_greeks(_book(), {"SMH": 632.0}, 0.043, {"SMH": 0.30})
        assert "SNDK" not in risk.by_underlying
        assert "SMH" in risk.by_underlying


class TestStressTest:
    def test_zero_shock_is_zero_pnl(self):
        rows = stress_test(_book(), SPOT, 0.043, VOL, spot_shocks=(0.0,), vol_shocks=(0.0,))
        assert rows[0]["pnl"] == pytest.approx(0.0, abs=1e-6)

    def test_downside_shock_loses_money(self):
        rows = stress_test(_book(), SPOT, 0.043, VOL, spot_shocks=(-0.15, 0.0, 0.15))
        by_shock = {r["spot_shock_pct"]: r["pnl"] for r in rows}
        assert by_shock[-15.0] < by_shock[0.0] < by_shock[15.0]


class TestSleeveStatus:
    def test_within_band(self):
        s = options_sleeve_status(1500, 15000, target_pct=10, max_pct=15)
        assert s["weight_pct"] == pytest.approx(10.0)
        assert s["status"] == "OK"

    def test_above_band(self):
        s = options_sleeve_status(3000, 15000, max_pct=15)
        assert s["status"] == "ABOVE"
