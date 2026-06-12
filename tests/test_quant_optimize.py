"""Tests for grid search and walk-forward parameter optimization."""

import numpy as np
import pandas as pd
import pytest

from src.quant.optimize import (
    ParamSpace,
    grid_search,
    walk_forward_optimize,
)
from src.quant.strategies_quant import MomentumStrategy


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


def _factory(params):
    return MomentumStrategy(lookback=params["lookback"], top_k=params["top_k"])


def test_param_space_combinations():
    space = ParamSpace({"lookback": [5, 10], "top_k": [1, 2]})
    combos = space.combinations()
    assert len(combos) == 4
    assert {"lookback": 5, "top_k": 1} in combos


def test_grid_search_returns_ranked_rows():
    rng = np.random.default_rng(0)
    panel = _panel({"AAA": list(rng.normal(0.003, 0.02, 150)),
                    "BBB": list(rng.normal(0.0, 0.02, 150))})
    space = ParamSpace({"lookback": [5, 10, 20], "top_k": [1]})
    result = grid_search(_factory, panel, space, rebalance="W", scorer="sharpe")
    assert len(result.rows) == 3
    # Rows are sorted by score descending.
    scores = [r.score for r in result.rows]
    assert scores == sorted(scores, reverse=True)
    assert result.best in [r.params for r in result.rows]


def test_grid_search_unknown_scorer_raises():
    panel = _panel({"AAA": [0.01] * 80, "BBB": [0.0] * 80})
    with pytest.raises(ValueError):
        grid_search(_factory, panel, ParamSpace({"lookback": [5], "top_k": [1]}),
                    scorer="nonsense")


def test_walk_forward_optimize_structure():
    rng = np.random.default_rng(3)
    panel = _panel({"AAA": list(rng.normal(0.002, 0.02, 240)),
                    "BBB": list(rng.normal(0.001, 0.02, 240))})
    space = ParamSpace({"lookback": [5, 15], "top_k": [1]})
    wfo = walk_forward_optimize(
        _factory, panel, space, train=80, test=30, rebalance="W", scorer="sharpe"
    )
    assert len(wfo.folds) >= 2
    # Each fold chose params from the grid.
    for fold in wfo.folds:
        assert fold.chosen_params["lookback"] in (5, 15)
    # Param-stability counts total to the number of folds.
    assert sum(wfo.param_stability["lookback"].values()) == len(wfo.folds)
    assert isinstance(wfo.overfit_warning, bool)
    assert wfo.oos_metrics.n_periods > 0
    # IS/OOS gap is mean_is - mean_oos.
    assert wfo.is_oos_gap == pytest.approx(wfo.mean_is_score - wfo.mean_oos_score)
