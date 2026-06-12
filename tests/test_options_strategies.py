"""Tests for option strategies, Level-2 compliance, payoff, and analysis."""

from datetime import date

import numpy as np
import pytest

from src.options import strategies as S
from src.options.models import BUY, CALL, PUT, SELL, OptionLeg, OptionStrategy

EVAL = date(2026, 6, 4)
EXP = date(2026, 7, 17)


# ── LEVEL-2 COMPLIANCE ───────────────────────────────────────────────────────

class TestValidateLevel2:
    def test_defined_risk_structures_pass(self):
        assert S.validate_level2(S.bull_put_spread("SMH", 580, 530, EXP)) == []
        assert S.validate_level2(S.bear_put_spread("SMH", 640, 590, EXP)) == []
        assert S.validate_level2(S.bear_call_spread("SMH", 640, 690, EXP)) == []
        assert S.validate_level2(S.bull_call_spread("SMH", 600, 650, EXP)) == []
        assert S.validate_level2(S.iron_condor("SMH", 520, 560, 700, 740, EXP)) == []

    def test_cash_secured_put_and_covered_call_pass(self):
        assert S.validate_level2(S.cash_secured_put("SNDK", 1500, EXP)) == []
        assert S.validate_level2(S.covered_call("SMH", 650, EXP, 1, shares=100)) == []

    def test_naked_call_rejected(self):
        naked = OptionStrategy(
            name="naked call", underlying="SMH",
            legs=[OptionLeg(underlying="SMH", right=CALL, strike=700, expiry=EXP, action=SELL)],
        )
        violations = S.validate_level2(naked)
        assert violations and "short CALL" in violations[0]

    def test_unbalanced_ratio_put_rejected(self):
        ratio = OptionStrategy(
            name="ratio", underlying="X",
            legs=[
                OptionLeg(underlying="X", right=PUT, strike=100, expiry=EXP, action=SELL, contracts=2),
                OptionLeg(underlying="X", right=PUT, strike=90, expiry=EXP, action=BUY, contracts=1),
            ],
        )
        assert S.validate_level2(ratio)

    def test_more_than_four_legs_rejected(self):
        legs = [OptionLeg(underlying="X", right=CALL, strike=100 + i, expiry=EXP, action=BUY) for i in range(5)]
        assert S.validate_level2(OptionStrategy(name="five", underlying="X", legs=legs))

    def test_covered_call_without_enough_stock_rejected(self):
        cc = S.covered_call("SMH", 650, EXP, contracts=2, shares=100)  # needs 200 shares
        assert S.validate_level2(cc)


# ── PAYOFF / ENTRY DEBIT ─────────────────────────────────────────────────────

class TestPayoffMath:
    def test_net_entry_debit_signs(self):
        spread = S.bull_call_spread("X", 100, 110, EXP)  # buy 100c, sell 110c
        debit = S.net_entry_debit(spread, leg_prices=[6.0, 2.0])
        # +6*100 (buy) -2*100 (sell) = 400 debit
        assert debit == pytest.approx(400.0)

    def test_long_call_payoff_floor_is_minus_debit(self):
        lc = S.long_call("X", 100, EXP)
        spots = np.array([0.0, 50.0, 100.0, 150.0])
        payoff = S.payoff_at_expiry(lc, spots, net_debit=500.0)
        assert payoff[0] == pytest.approx(-500.0)   # worthless -> lose premium
        assert payoff[-1] == pytest.approx(150 * 100 - 100 * 100 - 500.0)

    def test_breakevens_detected(self):
        lc = S.long_call("X", 100, EXP)
        spots = np.linspace(0, 200, 2001)
        payoff = S.payoff_at_expiry(lc, spots, net_debit=500.0)  # $5/share premium
        bes = S._breakevens(spots, payoff)
        assert any(abs(be - 105.0) < 0.5 for be in bes)


# ── ANALYSIS ─────────────────────────────────────────────────────────────────

class TestAnalyzeStrategy:
    def test_bull_put_spread_is_credit_and_bounded(self):
        bps = S.bull_put_spread("SMH", 580, 530, EXP)
        a = S.analyze_strategy(bps, spot=632.0, rate=0.043, vols=0.30, eval_date=EVAL)
        assert a.net_debit < 0           # credit received
        assert a.max_loss > -5000.0      # bounded by the 50-wide spread
        assert a.max_profit == pytest.approx(abs(a.net_debit), abs=1.0)
        assert 0.0 <= a.pop <= 1.0
        assert len(a.breakevens) == 1

    def test_long_call_is_debit_and_capped_loss(self):
        lc = S.long_call("SMH", 640, EXP)
        a = S.analyze_strategy(lc, spot=632.0, rate=0.043, vols=0.30, eval_date=EVAL)
        assert a.net_debit > 0
        assert a.max_loss == pytest.approx(-a.net_debit, abs=1.0)
        assert a.net_delta > 0

    def test_probability_of_profit_bounds(self):
        spots = np.linspace(1, 1000, 1000)
        payoff = np.where(spots > 600, 100.0, -100.0)
        pop = S.probability_of_profit(spots, payoff, spot=632.0, vol=0.3, t_years=0.12, rate=0.04)
        assert 0.0 <= pop <= 1.0

    def test_mark_strategy_changes_with_spot(self):
        lc = S.long_call("SMH", 640, EXP)
        low = S.mark_strategy(lc, 600, 0.043, 0.30, eval_date=EVAL)
        high = S.mark_strategy(lc, 660, 0.043, 0.30, eval_date=EVAL)
        assert high > low  # long call gains as spot rises


# ── FACTORY ──────────────────────────────────────────────────────────────────

class TestBuildNamedStrategy:
    def test_builds_known_structure(self):
        strat = S.build_named_strategy("bull-put-spread", "SMH", EXP, [580, 530], contracts=2)
        assert strat.name == "bull put spread"
        assert all(leg.contracts == 2 for leg in strat.legs)

    def test_wrong_strike_count_raises(self):
        with pytest.raises(ValueError):
            S.build_named_strategy("bull-put-spread", "SMH", EXP, [580], contracts=1)

    def test_unknown_structure_raises(self):
        with pytest.raises(ValueError):
            S.build_named_strategy("nope", "SMH", EXP, [100], contracts=1)


class TestBuyingPower:
    def test_bull_put_spread_reserves_max_loss_not_notional(self):
        # Regression: a defined-risk put spread flagged secured-by-cash must reserve
        # its max loss (the spread width), never the full short-strike notional.
        from src.options.models import SECURED_CASH
        from src.options.reporting import estimate_buying_power

        spread = S.bull_put_spread("SMH", 535, 515, EXP)
        spread.secured_by = SECURED_CASH  # even if mislabeled cash-secured
        analysis = S.analyze_strategy(spread, spot=585.0, rate=0.04, vols=0.55, eval_date=EVAL)
        bpr = estimate_buying_power(spread, analysis)
        assert bpr == pytest.approx(abs(analysis.max_loss), abs=1.0)
        assert bpr < 3000  # not the $53,500 cash-secured notional

    def test_genuine_cash_secured_put_reserves_full_notional(self):
        from src.options.reporting import estimate_buying_power

        csp = S.cash_secured_put("SNDK", 1500, EXP)
        analysis = S.analyze_strategy(csp, spot=1620.0, rate=0.04, vols=0.55, eval_date=EVAL)
        bpr = estimate_buying_power(csp, analysis)
        assert bpr == pytest.approx(1500 * 100, abs=1.0)

    def test_covered_call_reserves_no_cash(self):
        from src.options.reporting import estimate_buying_power

        cc = S.covered_call("SMH", 650, EXP, contracts=1, shares=100)
        analysis = S.analyze_strategy(cc, spot=585.0, rate=0.04, vols=0.50, eval_date=EVAL)
        assert estimate_buying_power(cc, analysis) == 0.0
