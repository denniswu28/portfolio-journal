"""Tests for the synthetic-chain option-structure backtest (no network)."""

import numpy as np
import pandas as pd
import pytest

from src.quant.options_backtest import (
    OptionBacktestConfig,
    backtest_option_structure,
)


def _daily(values, start="2022-01-03"):
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series([float(v) for v in values], index=idx)


def test_csp_on_calm_market_keeps_credit():
    # Noisy but flat around 100 -> 95-strike puts mostly expire worthless.
    rng = np.random.default_rng(0)
    prices = _daily(100 + np.cumsum(rng.normal(0, 0.3, 400)).clip(-8, 8))
    cfg = OptionBacktestConfig(structure="cash-secured-put", dte=30, otm=0.05,
                               take_profit_pct=50, close_by_dte=5, entry_freq_days=5)
    res = backtest_option_structure("AAA", cfg, prices=prices, rate=0.04)
    assert res.summary["n_trades"] > 0
    assert res.summary["total_pnl"] > 0          # net premium captured
    assert res.summary["win_rate_pct"] > 50.0


def test_csp_entry_is_a_credit():
    rng = np.random.default_rng(1)
    prices = _daily(100 + np.cumsum(rng.normal(0, 0.3, 200)).clip(-6, 6))
    cfg = OptionBacktestConfig(structure="cash-secured-put", dte=30, entry_freq_days=10)
    res = backtest_option_structure("AAA", cfg, prices=prices, rate=0.04)
    assert all(t.entry_credit >= 0 for t in res.trades)


def test_bull_put_spread_loss_is_bounded():
    # Steep crash after entries: short puts go deep ITM, but spread loss is capped.
    prices = _daily(np.linspace(100, 55, 300))
    cfg = OptionBacktestConfig(structure="bull-put-spread", dte=30, otm=0.03,
                               width_pct=0.05, stop_loss_pct=300, close_by_dte=2,
                               entry_freq_days=15)
    res = backtest_option_structure("BBB", cfg, prices=prices, rate=0.04)
    assert res.summary["n_trades"] > 0
    for trade in res.trades:
        # Realized loss never exceeds the structure's defined max loss (+ epsilon).
        assert trade.pnl >= trade.max_loss - 1.0


def test_result_structure_and_equity_curve():
    rng = np.random.default_rng(2)
    prices = _daily(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, 300))))
    res = backtest_option_structure("CCC", OptionBacktestConfig(entry_freq_days=10),
                                    prices=prices, rate=0.04)
    assert not res.equity_curve.empty
    assert res.metrics is not None
    assert {"n_trades", "win_rate_pct", "total_pnl"}.issubset(res.summary)
    assert "Theoretical" in res.note


def test_too_short_history_returns_empty():
    prices = _daily([100, 101, 102])
    res = backtest_option_structure("DDD", OptionBacktestConfig(), prices=prices, rate=0.04)
    assert res.summary.get("n_trades", 0) == 0
