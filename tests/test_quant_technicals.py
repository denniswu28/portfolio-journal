"""Known-answer tests for technical indicators."""

import math

import numpy as np
import pandas as pd
import pytest

from src.quant import technicals as ta


def _series(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.Series([float(v) for v in values], index=idx)


def test_sma_of_constant_is_constant():
    s = _series([5.0] * 10)
    out = ta.sma(s, window=3)
    assert out.dropna().eq(5.0).all()
    assert out.iloc[:2].isna().all()  # warmup


def test_ema_of_constant_is_constant():
    s = _series([7.0] * 10)
    out = ta.ema(s, span=4)
    assert out.dropna().round(6).eq(7.0).all()


def test_ma_cross_signal_directions():
    up = _series(list(range(1, 31)))  # strictly increasing -> fast above slow
    sig = ta.ma_cross_signal(up, fast=3, slow=10)
    assert sig.dropna().iloc[-1] == 1.0
    down = _series(list(range(30, 0, -1)))
    sig_down = ta.ma_cross_signal(down, fast=3, slow=10)
    assert sig_down.dropna().iloc[-1] == -1.0


def test_rsi_monotonic_uptrend_is_100():
    s = _series(list(range(1, 40)))
    out = ta.rsi(s, window=14)
    assert ta.latest_valid(out) == pytest.approx(100.0)


def test_rsi_monotonic_downtrend_is_0():
    s = _series(list(range(40, 1, -1)))
    out = ta.rsi(s, window=14)
    assert ta.latest_valid(out) == pytest.approx(0.0)


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    walk = 100 + np.cumsum(rng.normal(0, 1, 200))
    out = ta.rsi(_series(walk), window=14).dropna()
    assert (out >= 0).all() and (out <= 100).all()


def test_roc():
    s = _series([100, 110, 121])
    out = ta.roc(s, window=1)
    assert out.iloc[1] == pytest.approx(10.0)
    assert out.iloc[2] == pytest.approx(10.0)


def test_atr_nonnegative_and_warmup():
    high = _series([10, 11, 12, 13, 14, 15])
    low = _series([9, 9.5, 10, 11, 12, 13])
    close = _series([9.5, 10.5, 11.5, 12.5, 13.5, 14.5])
    out = ta.atr(high, low, close, window=3)
    assert (out.dropna() >= 0).all()


def test_true_range_known():
    high = _series([10, 12])
    low = _series([8, 9])
    close = _series([9, 11])
    tr = ta.true_range(high, low, close)
    # second bar: max(12-9, |12-9|, |9-9|) = 3
    assert tr.iloc[1] == pytest.approx(3.0)


def test_bollinger_pctb_at_band():
    rng = np.random.default_rng(1)
    walk = 100 + np.cumsum(rng.normal(0, 1, 60))
    bb = ta.bollinger(_series(walk), window=20, n_std=2.0)
    valid = bb.dropna()
    assert (valid["upper"] >= valid["mid"]).all()
    assert (valid["lower"] <= valid["mid"]).all()


def test_realized_vol_scales():
    rng = np.random.default_rng(2)
    close = _series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    rv = ta.realized_vol(close, window=20, periods_per_year=252).dropna()
    assert (rv > 0).all()
    # ~1% daily vol annualized ~ 16%.
    assert rv.iloc[-1] == pytest.approx(0.01 * math.sqrt(252), abs=0.08)


def test_realized_vol_percentile_in_unit_interval():
    rng = np.random.default_rng(3)
    close = _series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))))
    pct = ta.realized_vol_percentile(close, window=20, lookback=120).dropna()
    assert (pct >= 0).all() and (pct <= 1).all()


def test_relative_strength_sign():
    base = _series(100 * np.linspace(1.0, 1.3, 120))   # +30%
    bench = _series(100 * np.linspace(1.0, 1.1, 120))  # +10%
    rs = ta.relative_strength(base, bench, window=60).dropna()
    assert rs.iloc[-1] > 0  # outperforming


def test_support_resistance_bounds_price():
    s = _series([10, 12, 8, 15, 9, 11, 14, 7, 13, 10, 16, 6])
    sr = ta.support_resistance(s, window=4).dropna()
    assert (sr["support"] <= sr["resistance"]).all()
