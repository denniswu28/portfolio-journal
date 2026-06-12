"""Tests for period-aware quant metrics and result models."""

import math

import numpy as np
import pandas as pd
import pytest

from src.quant import models as qm


def _equity_from_returns(returns, start=100.0):
    values = [start]
    for r in returns:
        values.append(values[-1] * (1.0 + r))
    idx = pd.date_range("2024-01-05", periods=len(values), freq="W-FRI")
    return pd.Series(values, index=idx)


def test_to_returns_basic():
    eq = pd.Series([100.0, 110.0, 99.0])
    r = qm.to_returns(eq)
    assert list(round(x, 4) for x in r) == [0.1, -0.1]


def test_to_returns_too_short_is_empty():
    assert qm.to_returns(pd.Series([100.0])).empty


def test_total_return_pct():
    eq = pd.Series([100.0, 150.0])
    assert qm.total_return_pct(eq) == pytest.approx(50.0)


def test_cagr_doubling_in_one_year_weekly():
    # 52 weekly steps doubling -> ~100% CAGR.
    eq = pd.Series(np.linspace(100.0, 200.0, 53))
    assert qm.cagr_pct(eq, periods_per_year=52) == pytest.approx(100.0, abs=1.0)


def test_annualized_vol_scales_with_periods():
    returns = pd.Series([0.01, -0.01] * 26)
    vol = qm.annualized_volatility_pct(returns, periods_per_year=52)
    # std of +/-1% is ~1% per period; annualized ~ 1% * sqrt(52).
    assert vol == pytest.approx(1.0 * math.sqrt(52), abs=0.5)


def test_sharpe_constant_series_is_none():
    returns = pd.Series([0.005, 0.005, 0.005, 0.005])
    assert qm.sharpe_ratio(returns, periods_per_year=52, risk_free_rate=0.0) is None


def test_sharpe_positive_for_uptrend():
    returns = pd.Series([0.01, 0.012, 0.009, 0.011, 0.013])
    sharpe = qm.sharpe_ratio(returns, periods_per_year=52, risk_free_rate=0.0)
    assert sharpe is not None and sharpe > 0


def test_sortino_none_when_no_downside():
    returns = pd.Series([0.01, 0.02, 0.015])  # all above zero rf
    assert qm.sortino_ratio(returns, periods_per_year=52, risk_free_rate=0.0) is None


def test_sortino_exceeds_sharpe_when_downside_is_small():
    # Mostly up with a couple shallow dips: downside dev < total dev -> Sortino > Sharpe.
    returns = pd.Series([0.02, 0.02, -0.002, 0.02, -0.001, 0.02])
    sharpe = qm.sharpe_ratio(returns, periods_per_year=52, risk_free_rate=0.0)
    sortino = qm.sortino_ratio(returns, periods_per_year=52, risk_free_rate=0.0)
    assert sharpe is not None and sortino is not None
    assert sortino > sharpe


def test_max_drawdown_known_path():
    eq = pd.Series([100.0, 120.0, 60.0, 90.0])  # peak 120 -> trough 60 = 50% dd
    mdd, peak_idx, trough_idx = qm.max_drawdown(eq)
    assert mdd == pytest.approx(50.0)
    assert peak_idx == 1 and trough_idx == 2


def test_max_drawdown_monotonic_up_is_zero():
    eq = pd.Series([100.0, 101.0, 102.0])
    mdd, peak_idx, trough_idx = qm.max_drawdown(eq)
    assert mdd == 0.0 and peak_idx is None and trough_idx is None


def test_calmar_ratio():
    assert qm.calmar_ratio(20.0, 10.0) == pytest.approx(2.0)
    assert qm.calmar_ratio(20.0, 0.0) is None


def test_hit_rate():
    returns = pd.Series([0.01, -0.01, 0.01, 0.01])
    assert qm.hit_rate_pct(returns) == pytest.approx(75.0)


def test_compute_backtest_metrics_bundle():
    eq = _equity_from_returns([0.01, -0.005, 0.02, 0.0, 0.015])
    m = qm.compute_backtest_metrics(eq, periods_per_year=52, risk_free_rate=0.0)
    assert isinstance(m, qm.BacktestMetrics)
    assert m.n_periods == 5
    assert m.periods_per_year == 52
    assert m.total_return_pct == pytest.approx(qm.total_return_pct(eq))
    row = m.as_row()
    assert set(["cagr_pct", "sharpe", "sortino", "max_drawdown_pct"]).issubset(row)
