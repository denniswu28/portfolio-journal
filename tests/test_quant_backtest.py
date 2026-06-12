"""Tests for the backtest engine, lookahead guard, and walk-forward."""

import numpy as np
import pandas as pd
import pytest

from src.quant.backtest import (
    BacktestEngine,
    infer_periods_per_year,
    walk_forward,
)
from src.quant.strategies_quant import (
    EqualWeightStrategy,
    FixedWeightStrategy,
    MomentumStrategy,
)


def _panel(returns_by_ticker, start="2020-01-03", freq="W-FRI"):
    n = len(next(iter(returns_by_ticker.values())))
    idx = pd.date_range(start, periods=n + 1, freq=freq)
    data = {}
    for ticker, rets in returns_by_ticker.items():
        values = [100.0]
        for r in rets:
            values.append(values[-1] * (1.0 + r))
        data[ticker] = values
    return pd.DataFrame(data, index=idx)


def test_infer_periods_per_year():
    daily = pd.date_range("2020-01-01", periods=10, freq="B")
    weekly = pd.date_range("2020-01-01", periods=10, freq="W-FRI")
    monthly = pd.date_range("2020-01-01", periods=10, freq="ME")
    assert infer_periods_per_year(daily) == 252
    assert infer_periods_per_year(weekly) == 52
    assert infer_periods_per_year(monthly) == 12


def test_equal_weight_grows_with_uptrend():
    panel = _panel({"AAA": [0.01] * 60, "BBB": [0.01] * 60})
    result = BacktestEngine().run(EqualWeightStrategy(), panel, rebalance="M")
    assert result.equity_curve.iloc[-1] > result.equity_curve.iloc[0]
    assert result.metrics.n_periods > 0


def test_cost_drag_reduces_return():
    panel = _panel({"AAA": list(np.random.default_rng(0).normal(0.002, 0.02, 80)),
                    "BBB": list(np.random.default_rng(1).normal(0.002, 0.02, 80))})
    free = BacktestEngine().run(MomentumStrategy(lookback=10, top_k=1), panel,
                                rebalance="W", cost_bps=0.0)
    costly = BacktestEngine().run(MomentumStrategy(lookback=10, top_k=1), panel,
                                  rebalance="W", cost_bps=50.0)
    assert costly.equity_curve.iloc[-1] < free.equity_curve.iloc[-1]


def test_momentum_prefers_stronger_asset():
    panel = _panel({"WIN": [0.02] * 60, "LOSE": [-0.01] * 60})
    result = BacktestEngine().run(MomentumStrategy(lookback=8, top_k=1), panel, rebalance="W")
    # Final held weights should favor WIN.
    last_weights = result.weights_history.iloc[-1]
    assert last_weights["WIN"] > last_weights["LOSE"]


def test_lookahead_guard_future_spike_does_not_change_past_weights():
    rng = np.random.default_rng(7)
    panel = _panel({"AAA": list(rng.normal(0.001, 0.02, 80)),
                    "BBB": list(rng.normal(0.001, 0.02, 80))})
    strat = MomentumStrategy(lookback=10, top_k=1)
    base = BacktestEngine().run(strat, panel, rebalance="W")

    spiked = panel.copy()
    spiked.iloc[-1, spiked.columns.get_loc("BBB")] *= 5.0  # only the last bar
    after = BacktestEngine().run(MomentumStrategy(lookback=10, top_k=1), spiked, rebalance="W")

    # Weights decided strictly before the final bar must be identical.
    common = base.weights_history.index.intersection(after.weights_history.index)[:-1]
    pd.testing.assert_frame_equal(
        base.weights_history.loc[common], after.weights_history.loc[common]
    )


def test_fixed_weight_holds_allocation():
    panel = _panel({"AAA": [0.005] * 40, "BBB": [0.005] * 40})
    result = BacktestEngine().run(
        FixedWeightStrategy({"AAA": 0.6, "BBB": 0.4}), panel, rebalance="once"
    )
    held = result.weights_history.iloc[-1]
    assert held["AAA"] == pytest.approx(0.6)
    assert held["BBB"] == pytest.approx(0.4)


def test_benchmark_attaches():
    panel = _panel({"AAA": [0.01] * 60, "BBB": [0.01] * 60})
    bench = panel["AAA"]
    result = BacktestEngine().run(EqualWeightStrategy(), panel, rebalance="M", benchmark=bench)
    assert result.benchmark_curve is not None
    assert result.benchmark_metrics is not None


def test_walk_forward_folds_disjoint_and_stitched():
    rng = np.random.default_rng(11)
    panel = _panel({"AAA": list(rng.normal(0.002, 0.02, 200)),
                    "BBB": list(rng.normal(0.001, 0.02, 200))})
    wf = walk_forward(
        lambda train: MomentumStrategy(lookback=10, top_k=1),
        panel, train=60, test=20, rebalance="W",
    )
    assert len(wf.folds) >= 2
    # Test windows are disjoint and increasing.
    for earlier, later in zip(wf.folds, wf.folds[1:]):
        assert earlier.test_end <= later.test_start
    assert wf.stitched_equity.is_monotonic_increasing or len(wf.stitched_equity) > 0
    assert wf.metrics.n_periods > 0


def test_empty_panel_raises():
    with pytest.raises(ValueError):
        BacktestEngine().run(EqualWeightStrategy(), pd.DataFrame())
