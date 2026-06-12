"""Shared quant data models and period-aware performance metrics.

All return inputs here are **decimal** period returns (0.01 == 1%); metric outputs
exposed on dataclasses are stored as **percentages** for reporting parity with the
rest of the repo (see ``src/portfolio/analytics.py``). Annualization always takes an
explicit ``periods_per_year`` so a weekly backtest is scaled by 52, a daily one by
252 — never silently assuming daily data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

# Interval -> periods per year, matching optimizer.PERIODS_PER_YEAR.
PERIODS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12}
DEFAULT_RISK_FREE_RATE = 0.04


# ── PRIMITIVE METRIC FUNCTIONS (pure, period-aware) ──────────────────────────

def to_returns(equity: pd.Series) -> pd.Series:
    """Simple period returns (decimal) from an equity/price series."""
    eq = pd.Series(equity).astype(float).dropna()
    if len(eq) < 2:
        return pd.Series(dtype=float)
    return eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def total_return_pct(equity: pd.Series) -> float:
    """Cumulative return over the whole series, as a percentage."""
    eq = pd.Series(equity).astype(float).dropna()
    if len(eq) < 2 or eq.iloc[0] == 0:
        return 0.0
    return float((eq.iloc[-1] / eq.iloc[0] - 1.0) * 100.0)


def cagr_pct(equity: pd.Series, periods_per_year: int) -> float:
    """Compound annual growth rate (percent) using the period clock.

    Uses period count / periods_per_year for the year fraction so CAGR and the
    annualized Sharpe share one time base.
    """
    eq = pd.Series(equity).astype(float).dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return 0.0
    years = (len(eq) - 1) / periods_per_year
    if years <= 0:
        return 0.0
    growth = eq.iloc[-1] / eq.iloc[0]
    if growth <= 0:
        return -100.0
    return float((growth ** (1.0 / years) - 1.0) * 100.0)


def annualized_return_pct(returns: pd.Series, periods_per_year: int) -> float:
    """Arithmetic annualized return (percent) = mean period return * periods/yr."""
    r = pd.Series(returns).dropna()
    if r.empty:
        return 0.0
    return float(r.mean() * periods_per_year * 100.0)


def annualized_volatility_pct(returns: pd.Series, periods_per_year: int) -> float:
    """Annualized volatility (percent) from period returns."""
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * math.sqrt(periods_per_year) * 100.0)


def sharpe_ratio(
    returns: pd.Series,
    periods_per_year: int,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> Optional[float]:
    """Annualized Sharpe ratio, or None if undefined (constant / too short)."""
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return None
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return None
    per_period_rf = risk_free_rate / periods_per_year
    excess_mean = float(r.mean()) - per_period_rf
    return float(excess_mean / sd * math.sqrt(periods_per_year))


def sortino_ratio(
    returns: pd.Series,
    periods_per_year: int,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> Optional[float]:
    """Annualized Sortino ratio (downside-deviation denominator).

    Returns None when there is no downside (no excess return below zero) or the
    series is too short — an all-up series has an undefined Sortino.
    """
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return None
    per_period_rf = risk_free_rate / periods_per_year
    excess = r - per_period_rf
    downside = excess[excess < 0]
    if downside.empty:
        return None
    downside_dev = math.sqrt(float((downside ** 2).mean()))
    if downside_dev <= 0:
        return None
    return float(excess.mean() / downside_dev * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series):
    """Return (max_drawdown_pct positive, peak_index, trough_index)."""
    eq = pd.Series(equity).astype(float).dropna()
    if len(eq) < 1:
        return 0.0, None, None
    running_max = eq.cummax()
    drawdown = (eq - running_max) / running_max
    if drawdown.empty:
        return 0.0, None, None
    trough_idx = drawdown.idxmin()
    mdd = float(-drawdown.min() * 100.0)
    if mdd <= 0:
        return 0.0, None, None
    peak_idx = eq.loc[:trough_idx].idxmax()
    return mdd, peak_idx, trough_idx


def calmar_ratio(cagr_value_pct: float, max_drawdown_pct: float) -> Optional[float]:
    """Calmar ratio = CAGR / |max drawdown| (both in percent)."""
    if max_drawdown_pct <= 0:
        return None
    return float(cagr_value_pct / max_drawdown_pct)


def hit_rate_pct(returns: pd.Series) -> float:
    """Percentage of periods with a positive return."""
    r = pd.Series(returns).dropna()
    if r.empty:
        return 0.0
    return float((r > 0).mean() * 100.0)


# ── RESULT DATACLASSES ───────────────────────────────────────────────────────

@dataclass
class BacktestMetrics:
    """Headline performance numbers for an equity curve (all percentages)."""

    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    ann_return_pct: float = 0.0
    ann_volatility_pct: float = 0.0
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    max_drawdown_pct: float = 0.0
    calmar: Optional[float] = None
    hit_rate_pct: float = 0.0
    n_periods: int = 0
    periods_per_year: int = 252
    turnover_pct: Optional[float] = None

    def as_row(self) -> dict:
        """Flatten to a plain dict for CSV / tabulate output."""
        return {
            "total_return_pct": round(self.total_return_pct, 4),
            "cagr_pct": round(self.cagr_pct, 4),
            "ann_return_pct": round(self.ann_return_pct, 4),
            "ann_volatility_pct": round(self.ann_volatility_pct, 4),
            "sharpe": None if self.sharpe is None else round(self.sharpe, 4),
            "sortino": None if self.sortino is None else round(self.sortino, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "calmar": None if self.calmar is None else round(self.calmar, 4),
            "hit_rate_pct": round(self.hit_rate_pct, 4),
            "n_periods": self.n_periods,
            "turnover_pct": None if self.turnover_pct is None else round(self.turnover_pct, 4),
        }


@dataclass
class BacktestTrade:
    """One rebalance order recorded during a backtest."""

    date: pd.Timestamp
    ticker: str
    action: str  # "BUY" | "SELL"
    dollars: float
    weight_from: float
    weight_to: float
    cost: float = 0.0


@dataclass
class BacktestResult:
    """Full output of a single strategy backtest run."""

    strategy: str
    equity_curve: pd.Series
    returns: pd.Series
    metrics: BacktestMetrics
    weights_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: List[BacktestTrade] = field(default_factory=list)
    benchmark_curve: Optional[pd.Series] = None
    benchmark_metrics: Optional[BacktestMetrics] = None
    params: dict = field(default_factory=dict)
    periods_per_year: int = 252


def compute_backtest_metrics(
    equity: pd.Series,
    periods_per_year: int,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    turnover_pct: Optional[float] = None,
) -> BacktestMetrics:
    """Compute a BacktestMetrics bundle from an equity curve (period-aware)."""
    eq = pd.Series(equity).astype(float).dropna()
    returns = to_returns(eq)
    cagr_value = cagr_pct(eq, periods_per_year)
    mdd, _peak, _trough = max_drawdown(eq)
    return BacktestMetrics(
        total_return_pct=total_return_pct(eq),
        cagr_pct=cagr_value,
        ann_return_pct=annualized_return_pct(returns, periods_per_year),
        ann_volatility_pct=annualized_volatility_pct(returns, periods_per_year),
        sharpe=sharpe_ratio(returns, periods_per_year, risk_free_rate),
        sortino=sortino_ratio(returns, periods_per_year, risk_free_rate),
        max_drawdown_pct=mdd,
        calmar=calmar_ratio(cagr_value, mdd),
        hit_rate_pct=hit_rate_pct(returns),
        n_periods=int(len(returns)),
        periods_per_year=periods_per_year,
        turnover_pct=turnover_pct,
    )
