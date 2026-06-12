"""Technical indicators — pure pandas functions over price series.

Every function takes a ``pd.Series`` (or OHLC series) and returns an index-aligned
result with a NaN warmup prefix. No network, no third-party TA dependency, no hidden
state — so each indicator is unit-testable with a hand-built series. This keeps the
suite auditable per AGENTS.md (deterministic-first).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def _as_float(series: pd.Series) -> pd.Series:
    return pd.Series(series).astype(float)


# ── TREND ────────────────────────────────────────────────────────────────────

def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return _as_float(close).rolling(window=window, min_periods=window).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (min_periods=span for a clean warmup)."""
    return _as_float(close).ewm(span=span, min_periods=span, adjust=False).mean()


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram."""
    close = _as_float(close)
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def ma_cross_signal(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """+1 when fast SMA > slow SMA, -1 when below, 0 when equal/unknown."""
    fast_ma = sma(close, fast)
    slow_ma = sma(close, slow)
    signal = pd.Series(0, index=_as_float(close).index, dtype="float64")
    signal = signal.where(~(fast_ma > slow_ma), 1.0)
    signal = signal.where(~(fast_ma < slow_ma), -1.0)
    signal[fast_ma.isna() | slow_ma.isna()] = np.nan
    return signal


# ── MOMENTUM ─────────────────────────────────────────────────────────────────

def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0..100).

    All-gains warmup -> 100, all-losses -> 0, flat -> 50.
    """
    close = _as_float(close)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    out[avg_gain.isna() | avg_loss.isna()] = np.nan
    return out


def roc(close: pd.Series, window: int = 12) -> pd.Series:
    """Rate of change (percent) over ``window`` periods."""
    close = _as_float(close)
    return (close / close.shift(window) - 1.0) * 100.0


def relative_strength(
    close: pd.Series,
    benchmark: pd.Series,
    window: int = 63,
) -> pd.Series:
    """Excess trailing-window return vs a benchmark (decimal, positive = outperform)."""
    close = _as_float(close)
    benchmark = _as_float(benchmark).reindex(close.index).ffill()
    asset_ret = close / close.shift(window) - 1.0
    bench_ret = benchmark / benchmark.shift(window) - 1.0
    return asset_ret - bench_ret


# ── VOLATILITY ───────────────────────────────────────────────────────────────

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range from OHLC series."""
    high = _as_float(high)
    low = _as_float(low)
    prev_close = _as_float(close).shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """Average True Range via Wilder smoothing."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


def atr_from_close(close: pd.Series, window: int = 14) -> pd.Series:
    """Close-to-close ATR proxy when high/low are unavailable (graceful fallback)."""
    close = _as_float(close)
    tr = (close - close.shift(1)).abs()
    return tr.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


def bollinger(
    close: pd.Series,
    window: int = 20,
    n_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger bands with %B and bandwidth."""
    close = _as_float(close)
    mid = close.rolling(window=window, min_periods=window).mean()
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    upper = mid + n_std * std
    lower = mid - n_std * std
    width = (upper - lower)
    pctb = (close - lower) / width.replace(0.0, np.nan)
    bandwidth = width / mid.replace(0.0, np.nan)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "pctb": pctb, "bandwidth": bandwidth}
    )


def realized_vol(
    close: pd.Series,
    window: int = 20,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> pd.Series:
    """Rolling annualized realized volatility (decimal) from log returns."""
    close = _as_float(close)
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window=window, min_periods=window).std(ddof=1) * math.sqrt(
        periods_per_year
    )


def realized_vol_percentile(
    close: pd.Series,
    window: int = 20,
    lookback: int = 252,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> pd.Series:
    """Percentile rank (0..1) of current realized vol within a trailing lookback."""
    rv = realized_vol(close, window=window, periods_per_year=periods_per_year)
    return rv.rolling(window=lookback, min_periods=window).apply(
        lambda values: float((values <= values[-1]).mean()), raw=True
    )


# ── LEVELS ───────────────────────────────────────────────────────────────────

def support_resistance(close: pd.Series, window: int = 20) -> pd.DataFrame:
    """Rolling support (min) and resistance (max) levels over a window."""
    close = _as_float(close)
    support = close.rolling(window=window, min_periods=window).min()
    resistance = close.rolling(window=window, min_periods=window).max()
    return pd.DataFrame({"support": support, "resistance": resistance})


def latest_valid(series: pd.Series) -> Optional[float]:
    """Last non-NaN value of a series as a float, or None."""
    s = pd.Series(series).dropna()
    if s.empty:
        return None
    return float(s.iloc[-1])
