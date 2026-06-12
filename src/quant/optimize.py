"""Parameter optimization with anti-overfitting baked in.

``grid_search`` is full-sample and clearly labeled in-sample. ``walk_forward_optimize``
is the recommended entry point: it chooses parameters on each *train* fold and scores
them on the next, unseen *test* fold, stitching the out-of-sample curve and reporting
the IS-vs-OOS gap plus parameter stability so a fragile, overfit spike is visible.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from src.quant.backtest import BacktestEngine, Strategy, infer_periods_per_year
from src.quant.models import BacktestMetrics, compute_backtest_metrics

# Metric name -> how to read it off BacktestMetrics (higher is better).
_SCORERS = {
    "sharpe": lambda m: m.sharpe,
    "sortino": lambda m: m.sortino,
    "cagr": lambda m: m.cagr_pct,
    "calmar": lambda m: m.calmar,
    "total_return": lambda m: m.total_return_pct,
}


def _score(metrics: BacktestMetrics, scorer: str) -> float:
    if scorer not in _SCORERS:
        raise ValueError(f"Unknown scorer '{scorer}'. Options: {sorted(_SCORERS)}")
    value = _SCORERS[scorer](metrics)
    return float("-inf") if value is None else float(value)


@dataclass(frozen=True)
class ParamSpace:
    """A grid of parameter values to sweep."""

    grid: Dict[str, list]

    def combinations(self) -> List[dict]:
        if not self.grid:
            return [{}]
        keys = list(self.grid.keys())
        return [dict(zip(keys, values)) for values in itertools.product(*[self.grid[k] for k in keys])]


@dataclass
class ParamSweepRow:
    params: dict
    score: float
    metrics: BacktestMetrics


@dataclass
class ParamSweepResult:
    rows: List[ParamSweepRow]
    scorer: str
    best: dict = field(default_factory=dict)

    @property
    def best_row(self) -> Optional[ParamSweepRow]:
        return self.rows[0] if self.rows else None


def grid_search(
    strategy_factory: Callable[[dict], Strategy],
    price_df: pd.DataFrame,
    space: ParamSpace,
    *,
    rebalance: str = "M",
    cost_bps: float = 0.0,
    scorer: str = "sharpe",
    risk_free_rate: float = 0.04,
    periods_per_year: Optional[int] = None,
    start=None,
    end=None,
) -> ParamSweepResult:
    """Full-sample (in-sample) grid search. Use walk_forward_optimize for honesty."""
    if scorer not in _SCORERS:
        raise ValueError(f"Unknown scorer '{scorer}'. Options: {sorted(_SCORERS)}")
    engine = BacktestEngine()
    ppy = periods_per_year or infer_periods_per_year(price_df.index)
    rows: List[ParamSweepRow] = []
    for params in space.combinations():
        try:
            result = engine.run(
                strategy_factory(params), price_df,
                rebalance=rebalance, cost_bps=cost_bps,
                periods_per_year=ppy, risk_free_rate=risk_free_rate, start=start, end=end,
            )
            rows.append(ParamSweepRow(params, _score(result.metrics, scorer), result.metrics))
        except (ValueError, KeyError):
            continue
    rows.sort(key=lambda r: r.score, reverse=True)
    best = rows[0].params if rows else {}
    return ParamSweepResult(rows=rows, scorer=scorer, best=best)


@dataclass
class WFOFold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    chosen_params: dict
    is_score: float
    oos_score: float
    oos_metrics: BacktestMetrics


@dataclass
class WalkForwardOptimizeResult:
    folds: List[WFOFold]
    stitched_equity: pd.Series
    oos_metrics: BacktestMetrics
    scorer: str
    mean_is_score: float
    mean_oos_score: float
    is_oos_gap: float
    overfit_warning: bool
    param_stability: Dict[str, Dict] = field(default_factory=dict)
    periods_per_year: int = 252


def walk_forward_optimize(
    strategy_factory: Callable[[dict], Strategy],
    price_df: pd.DataFrame,
    space: ParamSpace,
    *,
    train: int,
    test: int,
    step: Optional[int] = None,
    rebalance: str = "M",
    cost_bps: float = 0.0,
    scorer: str = "sharpe",
    risk_free_rate: float = 0.04,
    periods_per_year: Optional[int] = None,
    overfit_gap_threshold: float = 0.5,
    initial: float = 100.0,
) -> WalkForwardOptimizeResult:
    """Choose params on each train fold, evaluate on the unseen next test fold."""
    price_df = price_df.sort_index()
    index = price_df.index
    n = len(index)
    step = step or test
    if n < train + test:
        raise ValueError("Not enough history for one train+test fold")
    ppy = periods_per_year or infer_periods_per_year(index)
    engine = BacktestEngine()

    folds: List[WFOFold] = []
    oos_returns: List[pd.Series] = []
    is_scores: List[float] = []
    oos_scores: List[float] = []
    stability: Dict[str, Counter] = {k: Counter() for k in space.grid}

    start_pos = 0
    while start_pos + train + test <= n:
        train_slice = price_df.iloc[start_pos : start_pos + train]
        test_start = index[start_pos + train]
        test_end = index[min(start_pos + train + test - 1, n - 1)]

        sweep = grid_search(
            strategy_factory, train_slice, space,
            rebalance=rebalance, cost_bps=cost_bps, scorer=scorer,
            risk_free_rate=risk_free_rate, periods_per_year=ppy,
        )
        if not sweep.rows:
            start_pos += step
            continue
        chosen = sweep.best
        is_score = sweep.best_row.score

        oos_result = engine.run(
            strategy_factory(chosen), price_df.iloc[: start_pos + train + test],
            rebalance=rebalance, cost_bps=cost_bps, start=test_start, end=test_end,
            periods_per_year=ppy, risk_free_rate=risk_free_rate, initial=initial,
        )
        oos_score = _score(oos_result.metrics, scorer)

        for key, value in chosen.items():
            stability[key][value] += 1
        folds.append(WFOFold(
            train_start=index[start_pos], train_end=index[start_pos + train - 1],
            test_start=test_start, test_end=test_end, chosen_params=chosen,
            is_score=is_score, oos_score=oos_score, oos_metrics=oos_result.metrics,
        ))
        is_scores.append(is_score)
        oos_scores.append(oos_score)
        oos_returns.append(oos_result.returns)
        start_pos += step

    if not folds:
        raise ValueError("No usable folds produced any result.")

    stitched_returns = pd.concat(oos_returns).sort_index()
    stitched_returns = stitched_returns[~stitched_returns.index.duplicated(keep="first")]
    stitched_equity = initial * (1.0 + stitched_returns).cumprod()
    oos_metrics = compute_backtest_metrics(stitched_equity, ppy, risk_free_rate=risk_free_rate)

    finite_is = [s for s in is_scores if s != float("-inf")]
    finite_oos = [s for s in oos_scores if s != float("-inf")]
    mean_is = float(sum(finite_is) / len(finite_is)) if finite_is else 0.0
    mean_oos = float(sum(finite_oos) / len(finite_oos)) if finite_oos else 0.0
    gap = mean_is - mean_oos
    overfit = gap > overfit_gap_threshold * (abs(mean_is) + 1e-9)

    return WalkForwardOptimizeResult(
        folds=folds,
        stitched_equity=stitched_equity,
        oos_metrics=oos_metrics,
        scorer=scorer,
        mean_is_score=mean_is,
        mean_oos_score=mean_oos,
        is_oos_gap=gap,
        overfit_warning=bool(overfit),
        param_stability={k: dict(v) for k, v in stability.items()},
        periods_per_year=ppy,
    )
