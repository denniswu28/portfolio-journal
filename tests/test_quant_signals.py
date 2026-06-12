"""Tests for SignalSet composition (no network — synthetic frames)."""

import numpy as np
import pandas as pd
import pytest

from src.quant.signals import (
    SignalConfig,
    SignalSet,
    compute_signal_set,
    compute_universe_signals,
)


def _series(values, start="2023-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series([float(v) for v in values], index=idx)


def _cfg():
    # Small windows so short synthetic series produce non-NaN readings.
    return SignalConfig(sma_fast=5, sma_slow=20, rsi_window=14, roc_window=10,
                        rv_window=10, rv_lookback=60, rs_window=20, bb_window=10)


def test_uptrend_flags_bullish():
    close = _series(np.linspace(100, 200, 120))
    ss = compute_signal_set("AAA", close, benchmark=None, config=_cfg())
    assert isinstance(ss, SignalSet)
    assert "above_200dma" in ss.flags
    assert "golden_cross" in ss.flags
    assert ss.composite["overall"] is not None and ss.composite["overall"] > 0


def test_downtrend_flags_bearish():
    close = _series(np.linspace(200, 100, 120))
    ss = compute_signal_set("BBB", close, benchmark=None, config=_cfg())
    assert "below_200dma" in ss.flags
    assert "death_cross" in ss.flags
    assert ss.composite["overall"] is not None and ss.composite["overall"] < 0


def test_relative_strength_outperform_flag():
    base = _series(100 * np.linspace(1.0, 1.4, 120))
    bench = _series(100 * np.linspace(1.0, 1.1, 120))
    ss = compute_signal_set("CCC", base, benchmark=bench, config=_cfg())
    assert ss.rel_strength["vs_benchmark"] is not None
    assert "outperforming" in ss.flags


def test_empty_series_returns_null_signalset():
    ss = compute_signal_set("DDD", pd.Series(dtype=float), config=_cfg())
    assert ss.close is None and ss.as_of is None and ss.flags == []


def test_to_dict_serializable():
    close = _series(np.linspace(100, 130, 80))
    ss = compute_signal_set("EEE", close, config=_cfg())
    d = ss.to_dict()
    assert d["ticker"] == "EEE"
    assert isinstance(d["flags"], list)
    assert d["as_of"] is None or isinstance(d["as_of"], str)


def test_compute_universe_signals_with_injected_history():
    idx = pd.date_range("2023-01-01", periods=120, freq="D")
    hist = pd.DataFrame(
        {
            "AAA": np.linspace(100, 200, 120),
            "BBB": np.linspace(200, 100, 120),
            "SPY": np.linspace(100, 110, 120),
        },
        index=idx,
    )
    out = compute_universe_signals(
        ["AAA", "BBB"], benchmark="SPY", config=_cfg(), price_history=hist
    )
    by_ticker = {s.ticker: s for s in out}
    assert "outperforming" in by_ticker["AAA"].flags
    assert "underperforming" in by_ticker["BBB"].flags


def test_universe_missing_ticker_is_null():
    idx = pd.date_range("2023-01-01", periods=60, freq="D")
    hist = pd.DataFrame({"SPY": np.linspace(100, 110, 60)}, index=idx)
    out = compute_universe_signals(["ZZZ"], benchmark="SPY", config=_cfg(), price_history=hist)
    assert out[0].ticker == "ZZZ" and out[0].close is None
