"""Generalized, lookahead-safe portfolio backtesting engine.

Design: a strategy decides target weights at the close of each rebalance bar using
**only** history up to and including that bar; those weights are applied to the
*next* bar's return onward (``weights.shift(1) * returns``). This makes lookahead
bias structurally impossible — a future price spike cannot change a past decision.

Generalizes the single-window dollar logic in ``backtest_baskets.py`` to arbitrary
horizons and rebalance frequencies, and adds walk-forward (out-of-sample) evaluation.
Metrics are period-aware via ``src/quant/models.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from src.quant.models import (
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    compute_backtest_metrics,
    to_returns,
)

_WEIGHT_EPS = 1e-6


# ── STRATEGY INTERFACE ───────────────────────────────────────────────────────

@dataclass
class RebalanceContext:
    """Inputs handed to a strategy at one rebalance bar.

    ``history`` is sliced to ``<= date`` by the engine, so a strategy physically
    cannot see the future.
    """

    date: pd.Timestamp
    history: pd.DataFrame          # prices up to and including ``date``
    current_weights: Dict[str, float]
    tickers: List[str]


@runtime_checkable
class Strategy(Protocol):
    name: str

    def warmup(self) -> int:
        """Bars of history required before the first allocation."""

    def target_weights(self, ctx: RebalanceContext) -> Dict[str, float]:
        """Return desired weights (long-only; values in [0, 1], cash = 1 - sum)."""


# ── HELPERS ──────────────────────────────────────────────────────────────────

def infer_periods_per_year(index: pd.DatetimeIndex) -> int:
    """Infer trading periods/year from the median spacing of a DatetimeIndex."""
    if len(index) < 3:
        return 252
    deltas = np.diff(index.values).astype("timedelta64[D]").astype(float)
    median_days = float(np.median(deltas))
    if median_days <= 2.0:
        return 252
    if median_days <= 10.0:
        return 52
    if median_days <= 45.0:
        return 12
    return 4


def _rebalance_dates(
    index: pd.DatetimeIndex,
    freq: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    warmup: int,
) -> pd.DatetimeIndex:
    """Pick rebalance bars within [start, end], honoring a warmup prefix."""
    if warmup >= len(index):
        return pd.DatetimeIndex([])
    earliest = index[warmup]
    lo = max(pd.Timestamp(start), pd.Timestamp(earliest))
    eligible = index[(index >= lo) & (index <= pd.Timestamp(end))]
    if len(eligible) == 0:
        return pd.DatetimeIndex([])

    token = freq.upper()
    if token in ("D", "DAY", "B"):
        return eligible
    if token in ("ONCE", "HOLD", "BH"):
        return pd.DatetimeIndex([eligible[0]])
    period = {"W": "W", "M": "M", "Q": "Q", "Y": "Y", "A": "Y"}.get(token)
    if period is None:
        raise ValueError(f"Unknown rebalance frequency: {freq}")
    buckets = eligible.to_period(period)
    frame = pd.DataFrame({"date": eligible}, index=range(len(eligible)))
    last = frame.groupby(buckets.values)["date"].max()
    return pd.DatetimeIndex(sorted(last.values))


def _clean_weights(raw: Dict[str, float], columns: List[str]) -> Dict[str, float]:
    """Long-only sanitize: drop NaN/negatives, cap total at 1.0 (rest is cash)."""
    cleaned = {c: 0.0 for c in columns}
    for ticker, weight in (raw or {}).items():
        sym = str(ticker).upper().strip()
        if sym in cleaned and weight is not None and np.isfinite(weight) and weight > 0:
            cleaned[sym] = float(weight)
    total = sum(cleaned.values())
    if total > 1.0 + _WEIGHT_EPS:
        cleaned = {c: w / total for c, w in cleaned.items()}
    return cleaned


# ── ENGINE ───────────────────────────────────────────────────────────────────

class BacktestEngine:
    """Run a Strategy over a price panel and produce a BacktestResult."""

    def run(
        self,
        strategy: Strategy,
        price_df: pd.DataFrame,
        *,
        rebalance: str = "M",
        cost_bps: float = 0.0,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
        periods_per_year: Optional[int] = None,
        risk_free_rate: float = 0.04,
        benchmark: Optional[pd.Series] = None,
        initial: float = 100.0,
    ) -> BacktestResult:
        if price_df is None or price_df.empty:
            raise ValueError("price_df is empty")

        price_df = price_df.sort_index()
        price_df = price_df.loc[:, [c for c in price_df.columns if price_df[c].notna().any()]]
        columns = list(price_df.columns)
        rets = price_df.pct_change().replace([np.inf, -np.inf], np.nan)

        start = pd.Timestamp(start) if start is not None else price_df.index[0]
        end = pd.Timestamp(end) if end is not None else price_df.index[-1]
        ppy = periods_per_year or infer_periods_per_year(price_df.index)

        warmup = max(0, int(strategy.warmup()))
        reb_dates = _rebalance_dates(price_df.index, rebalance, start, end, warmup)
        if len(reb_dates) == 0:
            raise ValueError("No rebalance dates available (history too short for warmup).")
        reb_set = set(reb_dates)

        weights_history = pd.DataFrame(0.0, index=price_df.index, columns=columns)
        current = {c: 0.0 for c in columns}
        trades: List[BacktestTrade] = []
        turnover_at: Dict[pd.Timestamp, float] = {}

        for date in price_df.index:
            if date in reb_set:
                ctx = RebalanceContext(
                    date=date,
                    history=price_df.loc[:date],   # <= date only: lookahead guard
                    current_weights=dict(current),
                    tickers=columns,
                )
                target = _clean_weights(strategy.target_weights(ctx), columns)
                turnover = sum(abs(target[c] - current.get(c, 0.0)) for c in columns)
                turnover_at[date] = turnover
                for c in columns:
                    delta = target[c] - current.get(c, 0.0)
                    if abs(delta) > _WEIGHT_EPS:
                        trades.append(
                            BacktestTrade(
                                date=date,
                                ticker=c,
                                action="BUY" if delta > 0 else "SELL",
                                dollars=float(delta * initial),
                                weight_from=float(current.get(c, 0.0)),
                                weight_to=float(target[c]),
                                cost=float(abs(delta) * cost_bps / 1e4 * initial),
                            )
                        )
                current = target
            weights_history.loc[date] = [current.get(c, 0.0) for c in columns]

        gross_ret = (weights_history.shift(1) * rets).sum(axis=1)
        cost_series = pd.Series(0.0, index=price_df.index)
        for date, turnover in turnover_at.items():
            cost_series.loc[date] = turnover * cost_bps / 1e4
        port_ret = (gross_ret - cost_series)

        first_reb = reb_dates[0]
        port_ret = port_ret[port_ret.index >= first_reb].dropna()
        equity = initial * (1.0 + port_ret).cumprod()

        avg_turnover_pct = (
            float(np.mean(list(turnover_at.values())) * 100.0) if turnover_at else 0.0
        )
        metrics = compute_backtest_metrics(
            equity, ppy, risk_free_rate=risk_free_rate, turnover_pct=avg_turnover_pct
        )

        bench_curve, bench_metrics = None, None
        if benchmark is not None and not pd.Series(benchmark).dropna().empty:
            bench_curve, bench_metrics = self._benchmark(
                benchmark, equity.index, initial, ppy, risk_free_rate
            )

        return BacktestResult(
            strategy=getattr(strategy, "name", strategy.__class__.__name__),
            equity_curve=equity,
            returns=to_returns(equity),
            metrics=metrics,
            weights_history=weights_history.loc[equity.index],
            trades=trades,
            benchmark_curve=bench_curve,
            benchmark_metrics=bench_metrics,
            params=dict(getattr(strategy, "params", {})),
            periods_per_year=ppy,
        )

    @staticmethod
    def _benchmark(benchmark, equity_index, initial, ppy, risk_free_rate):
        bench = pd.Series(benchmark).astype(float).reindex(
            equity_index.union(pd.Series(benchmark).index)
        ).ffill().reindex(equity_index)
        bench = bench.dropna()
        if bench.empty:
            return None, None
        bench_curve = initial * bench / bench.iloc[0]
        bench_metrics = compute_backtest_metrics(bench_curve, ppy, risk_free_rate=risk_free_rate)
        return bench_curve, bench_metrics


# ── WALK-FORWARD (out-of-sample) ─────────────────────────────────────────────

@dataclass
class WalkForwardFold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    result: BacktestResult


@dataclass
class WalkForwardResult:
    """Stitched out-of-sample equity curve across disjoint test folds."""

    stitched_equity: pd.Series
    metrics: BacktestMetrics
    folds: List[WalkForwardFold] = field(default_factory=list)
    periods_per_year: int = 252


def walk_forward(
    strategy_factory: Callable[[pd.DataFrame], Strategy],
    price_df: pd.DataFrame,
    *,
    train: int,
    test: int,
    step: Optional[int] = None,
    rebalance: str = "M",
    cost_bps: float = 0.0,
    risk_free_rate: float = 0.04,
    periods_per_year: Optional[int] = None,
    initial: float = 100.0,
) -> WalkForwardResult:
    """Roll a (train -> test) window across the panel, stitching OOS test curves.

    ``strategy_factory`` receives the *train* slice only (to fit/parameterize) and
    returns a Strategy; the engine then evaluates it on the immediately following,
    unseen *test* slice. Test folds are disjoint and chained multiplicatively.
    """
    price_df = price_df.sort_index()
    index = price_df.index
    n = len(index)
    step = step or test
    if train <= 0 or test <= 0:
        raise ValueError("train and test must be positive")
    if n < train + test:
        raise ValueError("Not enough history for one train+test fold")

    ppy = periods_per_year or infer_periods_per_year(index)
    engine = BacktestEngine()
    folds: List[WalkForwardFold] = []
    oos_returns: List[pd.Series] = []

    start_pos = 0
    while start_pos + train + test <= n:
        train_slice = price_df.iloc[start_pos : start_pos + train]
        test_start = index[start_pos + train]
        test_end = index[min(start_pos + train + test - 1, n - 1)]

        strategy = strategy_factory(train_slice)
        # Pass full history up to test_end so warmup/history is available, but only
        # evaluate (rebalance + accrue returns) within the test window.
        result = engine.run(
            strategy,
            price_df.iloc[: start_pos + train + test],
            rebalance=rebalance,
            cost_bps=cost_bps,
            start=test_start,
            end=test_end,
            periods_per_year=ppy,
            risk_free_rate=risk_free_rate,
            initial=initial,
        )
        folds.append(
            WalkForwardFold(
                train_start=index[start_pos],
                train_end=index[start_pos + train - 1],
                test_start=test_start,
                test_end=test_end,
                result=result,
            )
        )
        oos_returns.append(result.returns)
        start_pos += step

    stitched_returns = pd.concat(oos_returns).sort_index()
    stitched_returns = stitched_returns[~stitched_returns.index.duplicated(keep="first")]
    stitched_equity = initial * (1.0 + stitched_returns).cumprod()
    metrics = compute_backtest_metrics(stitched_equity, ppy, risk_free_rate=risk_free_rate)

    return WalkForwardResult(
        stitched_equity=stitched_equity,
        metrics=metrics,
        folds=folds,
        periods_per_year=ppy,
    )
