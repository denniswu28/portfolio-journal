"""Per-ticker technical SignalSet composition.

Combines the pure indicators in ``technicals.py`` into one structured, serializable
``SignalSet`` per ticker (trend / momentum / volatility / relative-strength readings,
a composite score, and human-readable flags). ``compute_universe_signals`` batches a
single ``get_price_history`` download for a whole universe and a benchmark.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from src.data_ingestion.market_data import get_price_history
from src.quant import technicals as ta


@dataclass(frozen=True)
class SignalConfig:
    """Indicator windows used to build a SignalSet."""

    sma_fast: int = 50
    sma_slow: int = 200
    rsi_window: int = 14
    roc_window: int = 63
    atr_window: int = 14
    bb_window: int = 20
    rv_window: int = 20
    rv_lookback: int = 252
    rs_window: int = 63
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    overbought: float = 70.0
    oversold: float = 30.0
    high_vol_pctile: float = 0.80
    low_vol_pctile: float = 0.20
    near_level_pct: float = 0.03  # within 3% of support/resistance


@dataclass(frozen=True)
class SignalSet:
    """Structured technical readings for one ticker at a point in time."""

    ticker: str
    as_of: Optional[pd.Timestamp]
    close: Optional[float]
    trend: Dict[str, Optional[float]] = field(default_factory=dict)
    momentum: Dict[str, Optional[float]] = field(default_factory=dict)
    volatility: Dict[str, Optional[float]] = field(default_factory=dict)
    rel_strength: Dict[str, Optional[float]] = field(default_factory=dict)
    composite: Dict[str, Optional[float]] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["as_of"] = None if self.as_of is None else str(self.as_of)
        return out


def _sign(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


def _mean_ignoring_none(values: List[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return float(sum(present) / len(present))


def compute_signal_set(
    ticker: str,
    close: pd.Series,
    benchmark: Optional[pd.Series] = None,
    config: SignalConfig = SignalConfig(),
    periods_per_year: int = ta.TRADING_DAYS_PER_YEAR,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
) -> SignalSet:
    """Build a SignalSet from a close series (and optional OHLC + benchmark)."""
    close = pd.Series(close).astype(float).dropna()
    if close.empty:
        return SignalSet(ticker=ticker.upper(), as_of=None, close=None)

    last_close = float(close.iloc[-1])
    as_of = close.index[-1]

    sma_fast_val = ta.latest_valid(ta.sma(close, config.sma_fast))
    sma_slow_val = ta.latest_valid(ta.sma(close, config.sma_slow))
    macd_hist = ta.latest_valid(
        ta.macd(close, config.macd_fast, config.macd_slow, config.macd_signal)["hist"]
    )
    ma_regime = ta.latest_valid(ta.ma_cross_signal(close, config.sma_fast, config.sma_slow))

    rsi_val = ta.latest_valid(ta.rsi(close, config.rsi_window))
    roc_val = ta.latest_valid(ta.roc(close, config.roc_window))

    if high is not None and low is not None:
        atr_val = ta.latest_valid(ta.atr(high, low, close, config.atr_window))
    else:
        atr_val = ta.latest_valid(ta.atr_from_close(close, config.atr_window))
    rv_val = ta.latest_valid(ta.realized_vol(close, config.rv_window, periods_per_year))
    rv_pctile = ta.latest_valid(
        ta.realized_vol_percentile(close, config.rv_window, config.rv_lookback, periods_per_year)
    )
    bb = ta.bollinger(close, config.bb_window)
    bb_pctb = ta.latest_valid(bb["pctb"])
    sr = ta.support_resistance(close, config.bb_window)
    support = ta.latest_valid(sr["support"])
    resistance = ta.latest_valid(sr["resistance"])

    rs_val = None
    if benchmark is not None and not pd.Series(benchmark).dropna().empty:
        rs_val = ta.latest_valid(ta.relative_strength(close, benchmark, config.rs_window))

    trend = {
        "sma_fast": sma_fast_val,
        "sma_slow": sma_slow_val,
        "macd_hist": macd_hist,
        "ma_regime": ma_regime,
        "above_slow_ma": None if sma_slow_val is None else float(last_close > sma_slow_val),
    }
    momentum = {"rsi": rsi_val, "roc_pct": roc_val}
    volatility = {
        "atr": atr_val,
        "atr_pct": None if (atr_val is None or last_close == 0) else atr_val / last_close * 100.0,
        "realized_vol": rv_val,
        "rv_percentile": rv_pctile,
        "bb_pctb": bb_pctb,
        "support": support,
        "resistance": resistance,
    }
    rel_strength = {"vs_benchmark": rs_val}

    # Composite scores in [-1, 1].
    trend_score = _mean_ignoring_none(
        [
            ma_regime,
            _sign(macd_hist),
            None if sma_slow_val is None else (1.0 if last_close > sma_slow_val else -1.0),
        ]
    )
    momentum_score = _mean_ignoring_none(
        [
            None if rsi_val is None else max(-1.0, min(1.0, (rsi_val - 50.0) / 50.0)),
            _sign(roc_val),
        ]
    )
    overall = _mean_ignoring_none([trend_score, momentum_score, _sign(rs_val)])
    composite = {"trend": trend_score, "momentum": momentum_score, "overall": overall}

    flags = _build_flags(
        last_close, sma_slow_val, ma_regime, rsi_val, rv_pctile, rs_val, macd_hist,
        support, resistance, config,
    )

    return SignalSet(
        ticker=ticker.upper(),
        as_of=as_of,
        close=last_close,
        trend=trend,
        momentum=momentum,
        volatility=volatility,
        rel_strength=rel_strength,
        composite=composite,
        flags=flags,
    )


def _build_flags(
    last_close, sma_slow_val, ma_regime, rsi_val, rv_pctile, rs_val, macd_hist,
    support, resistance, config: SignalConfig,
) -> List[str]:
    flags: List[str] = []
    if sma_slow_val is not None:
        flags.append("above_200dma" if last_close > sma_slow_val else "below_200dma")
    if ma_regime is not None:
        if ma_regime > 0:
            flags.append("golden_cross")
        elif ma_regime < 0:
            flags.append("death_cross")
    if rsi_val is not None:
        if rsi_val >= config.overbought:
            flags.append("overbought")
        elif rsi_val <= config.oversold:
            flags.append("oversold")
    if rv_pctile is not None:
        if rv_pctile >= config.high_vol_pctile:
            flags.append("high_vol_regime")
        elif rv_pctile <= config.low_vol_pctile:
            flags.append("low_vol_regime")
    if rs_val is not None:
        flags.append("outperforming" if rs_val > 0 else "underperforming")
    if macd_hist is not None:
        flags.append("macd_bullish" if macd_hist > 0 else "macd_bearish")
    if resistance and last_close >= resistance * (1.0 - config.near_level_pct):
        flags.append("near_resistance")
    if support and last_close <= support * (1.0 + config.near_level_pct):
        flags.append("near_support")
    return flags


def compute_universe_signals(
    tickers: List[str],
    period: str = "2y",
    interval: str = "1d",
    benchmark: str = "SPY",
    config: SignalConfig = SignalConfig(),
    price_history: Optional[pd.DataFrame] = None,
) -> List[SignalSet]:
    """Compute SignalSets for a universe with one batched price download.

    Pass ``price_history`` to skip the network (tests/offline); otherwise a single
    ``get_price_history`` call fetches tickers + benchmark together. ATR uses the
    close-to-close proxy here since the batched frame is close-only.
    """
    symbols = []
    for raw in [*tickers, benchmark]:
        sym = str(raw).upper().strip()
        if sym and sym not in symbols:
            symbols.append(sym)

    if price_history is None:
        price_history = get_price_history(symbols, period=period, interval=interval)
    if price_history is None or price_history.empty:
        return [SignalSet(ticker=str(t).upper(), as_of=None, close=None) for t in tickers]

    periods_per_year = {"1d": 252, "1wk": 52, "1mo": 12}.get(interval, 252)
    bench_symbol = benchmark.upper().strip()
    bench_series = price_history[bench_symbol] if bench_symbol in price_history.columns else None

    results: List[SignalSet] = []
    for raw in tickers:
        sym = str(raw).upper().strip()
        if sym not in price_history.columns:
            results.append(SignalSet(ticker=sym, as_of=None, close=None))
            continue
        results.append(
            compute_signal_set(
                ticker=sym,
                close=price_history[sym],
                benchmark=bench_series,
                config=config,
                periods_per_year=periods_per_year,
            )
        )
    return results
