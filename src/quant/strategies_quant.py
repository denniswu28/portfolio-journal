"""Concrete backtest strategies.

Each satisfies the ``Strategy`` protocol in ``backtest.py``. ``SleeveRebalanceStrategy``
reuses the real ``build_rebalance_plan`` optimizer so a backtest of the sleeve policy
matches what ``main.py rebalance-weights`` would produce live.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.portfolio.optimizer import METHODS, SleeveDefinition, build_rebalance_plan
from src.quant.backtest import RebalanceContext


class FixedWeightStrategy:
    """Hold a fixed target weight vector (set once, held)."""

    def __init__(self, weights: Dict[str, float], name: str = "fixed-weight"):
        self.name = name
        self.weights = {k.upper(): float(v) for k, v in weights.items()}
        self.params = {"weights": self.weights}

    def warmup(self) -> int:
        return 1

    def target_weights(self, ctx: RebalanceContext) -> Dict[str, float]:
        return dict(self.weights)


class EqualWeightStrategy:
    """Equal-weight across all tickers with data (rebalanced on the engine's clock)."""

    def __init__(self, name: str = "equal-weight"):
        self.name = name
        self.params: Dict = {}

    def warmup(self) -> int:
        return 1

    def target_weights(self, ctx: RebalanceContext) -> Dict[str, float]:
        available = [
            c for c in ctx.tickers
            if not ctx.history[c].dropna().empty and np.isfinite(ctx.history[c].iloc[-1])
        ]
        if not available:
            return {}
        w = 1.0 / len(available)
        return {c: w for c in available}


class MomentumStrategy:
    """Hold the top-k tickers by trailing return; equal-weight; cash if none positive."""

    def __init__(self, lookback: int = 63, top_k: int = 3, name: Optional[str] = None):
        self.lookback = int(lookback)
        self.top_k = int(top_k)
        self.name = name or f"momentum-{lookback}-{top_k}"
        self.params = {"lookback": self.lookback, "top_k": self.top_k}

    def warmup(self) -> int:
        return self.lookback + 1

    def target_weights(self, ctx: RebalanceContext) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for c in ctx.tickers:
            series = ctx.history[c].dropna()
            if len(series) > self.lookback:
                past = series.iloc[-1 - self.lookback]
                if past > 0:
                    scores[c] = series.iloc[-1] / past - 1.0
        winners = [t for t, s in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
                   if s > 0][: self.top_k]
        if not winners:
            return {}
        w = 1.0 / len(winners)
        return {t: w for t in winners}


class SleeveRebalanceStrategy:
    """Optimizer-backed sleeve allocation (reuses build_rebalance_plan)."""

    def __init__(
        self,
        sleeves: List[SleeveDefinition],
        method: str = "erc",
        cash_target_pct: float = 10.0,
        periods_per_year: int = 52,
        min_observations: int = 52,
        risk_free_rate: float = 0.04,
        name: Optional[str] = None,
    ):
        if method not in METHODS:
            raise ValueError(f"Unknown method: {method}")
        self.sleeves = sleeves
        self.method = method
        self.cash_target_pct = cash_target_pct
        self.periods_per_year = periods_per_year
        self.min_observations = min_observations
        self.risk_free_rate = risk_free_rate
        self.name = name or f"sleeve-{method}"
        self.params = {"method": method, "cash_target_pct": cash_target_pct}

    def warmup(self) -> int:
        return self.min_observations + 1

    def target_weights(self, ctx: RebalanceContext) -> Dict[str, float]:
        proxies = [s.proxy.upper() for s in self.sleeves if s.proxy.upper() in ctx.history.columns]
        if not proxies:
            return {}
        history = ctx.history[proxies]
        try:
            plan = build_rebalance_plan(
                sleeves=self.sleeves,
                price_history=history,
                method=self.method,
                cash_target_pct=self.cash_target_pct,
                risk_free_rate=self.risk_free_rate,
                periods_per_year=self.periods_per_year,
                min_observations=self.min_observations,
            )
        except (ValueError, KeyError):
            return {}
        return {
            str(row["proxy"]).upper(): float(row["target_weight_pct"]) / 100.0
            for row in plan["rows"]
            if row.get("proxy")
        }
