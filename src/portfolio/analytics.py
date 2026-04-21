"""
analytics.py - All portfolio calculations in one place.

Sections:
  1. Cost Basis
  2. P&L (Realized + Unrealized)
  3. Performance Metrics
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from src.data_ingestion.models import PerformanceMetrics, PortfolioSnapshot, Position, Trade


# ── 1. COST BASIS ─────────────────────────────────────────────────────────────

def calculate_avg_cost_basis(trades: List[Trade], ticker: str) -> float:
    """
    Compute the average cost basis for a ticker from trade history.

    Uses a weighted-average approach: BUY trades add to cost pool,
    SELL trades reduce the share count while preserving the average cost.

    Args:
        trades: Full trade history (all tickers).
        ticker: The specific ticker to compute cost basis for.

    Returns:
        Average cost per share, or 0.0 if no BUY trades found.
    """
    total_shares = 0
    total_cost = 0.0

    for trade in sorted(trades, key=lambda t: t.timestamp):
        if trade.ticker != ticker:
            continue
        if trade.action == "BUY":
            total_cost += trade.shares * trade.price
            total_shares += trade.shares
        elif trade.action == "SELL" and total_shares > 0:
            avg = total_cost / total_shares
            total_cost -= avg * trade.shares
            total_shares = max(0, total_shares - trade.shares)

    return (total_cost / total_shares) if total_shares > 0 else 0.0


# ── 2. P&L ────────────────────────────────────────────────────────────────────

def unrealized_pnl(position: Position) -> Dict[str, float]:
    """
    Compute unrealized P&L for a single position.

    Args:
        position: An enriched Position object.

    Returns:
        Dict with keys 'dollars' and 'percent'.
    """
    pnl_dollars = (position.current_price - position.avg_cost_basis) * position.shares
    pnl_pct = (
        ((position.current_price / position.avg_cost_basis) - 1) * 100
        if position.avg_cost_basis
        else 0.0
    )
    return {"dollars": pnl_dollars, "percent": pnl_pct}


def realized_pnl(trades: List[Trade]) -> float:
    """
    Compute total realized P&L from all SELL trades using average cost method.

    Args:
        trades: Full trade history.

    Returns:
        Total realized P&L in dollars.
    """
    # Group BUY trades per ticker to track running avg cost
    avg_costs: Dict[str, float] = {}
    shares_held: Dict[str, int] = {}
    total_realized = 0.0

    for trade in sorted(trades, key=lambda t: t.timestamp):
        ticker = trade.ticker
        if trade.action == "BUY":
            prev_shares = shares_held.get(ticker, 0)
            prev_avg = avg_costs.get(ticker, 0.0)
            new_shares = prev_shares + trade.shares
            new_avg = (
                (prev_avg * prev_shares + trade.price * trade.shares) / new_shares
                if new_shares > 0
                else 0.0
            )
            avg_costs[ticker] = new_avg
            shares_held[ticker] = new_shares
        elif trade.action == "SELL":
            avg_cost = avg_costs.get(ticker, trade.price)
            pnl = (trade.price - avg_cost) * trade.shares
            total_realized += pnl
            shares_held[ticker] = max(0, shares_held.get(ticker, 0) - trade.shares)

    return total_realized


def total_pnl(snapshot: PortfolioSnapshot) -> Dict[str, float]:
    """
    Return the combined unrealized P&L from the snapshot.

    Args:
        snapshot: A PortfolioSnapshot with enriched positions.

    Returns:
        Dict with 'dollars', 'percent', and per-ticker breakdown.
    """
    total_dollars = sum(p.unrealized_pnl for p in snapshot.positions)
    total_invested = sum(p.avg_cost_basis * p.shares for p in snapshot.positions)
    total_pct = (total_dollars / total_invested * 100) if total_invested else 0.0

    per_ticker = {
        p.ticker: unrealized_pnl(p) for p in snapshot.positions
    }
    return {
        "dollars": total_dollars,
        "percent": total_pct,
        "per_ticker": per_ticker,
    }


# ── 3. PERFORMANCE METRICS ────────────────────────────────────────────────────

def compute_metrics(
    snapshots: List[PortfolioSnapshot],
    trades: Optional[List[Trade]] = None,
    risk_free_rate: float = 0.05,
) -> PerformanceMetrics:
    """
    Compute portfolio performance metrics from a time series of snapshots.

    Computes:
    - cumulative_return_pct
    - daily_returns (list of % changes between consecutive snapshots)
    - sharpe_ratio  (annualized, using provided risk_free_rate)
    - max_drawdown_pct
    - win_rate, avg_win_pct, avg_loss_pct (from closed trades)
    - concentration_top3_pct

    Args:
        snapshots: Time-ordered list of PortfolioSnapshot objects.
        trades: Optional trade history for win/loss stats.
        risk_free_rate: Annual risk-free rate (default 5%).

    Returns:
        A PerformanceMetrics object.
    """
    if not snapshots:
        return PerformanceMetrics()

    values = [s.total_portfolio_value for s in snapshots]

    # Cumulative return
    cumulative_return_pct = (
        ((values[-1] / values[0]) - 1) * 100 if values[0] else 0.0
    )

    # Daily returns
    daily_returns = []
    for i in range(1, len(values)):
        if values[i - 1]:
            daily_returns.append(((values[i] / values[i - 1]) - 1) * 100)

    # Sharpe ratio (annualized, assuming daily snapshots)
    sharpe_ratio = _compute_sharpe(daily_returns, risk_free_rate)

    # Max drawdown
    max_drawdown_pct = _compute_max_drawdown(values)

    # Win/loss stats from trade history
    win_rate_pct, avg_win_pct, avg_loss_pct, winning, losing = _compute_trade_stats(
        trades or []
    )

    # Concentration: weight of top 3 holdings in latest snapshot
    concentration_top3_pct = _compute_concentration(snapshots[-1], top_n=3)

    return PerformanceMetrics(
        cumulative_return_pct=cumulative_return_pct,
        daily_returns=daily_returns,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        win_rate_pct=win_rate_pct,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        concentration_top3_pct=concentration_top3_pct,
        total_trades=winning + losing,
        winning_trades=winning,
        losing_trades=losing,
    )


# ── PRIVATE HELPERS ───────────────────────────────────────────────────────────

def _compute_sharpe(
    daily_returns: List[float], annual_risk_free_rate: float = 0.05
) -> Optional[float]:
    """Annualized Sharpe ratio from daily returns (in %)."""
    if len(daily_returns) < 2:
        return None
    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0
    if std_dev == 0:
        return None
    # Convert annual risk-free rate to daily %
    daily_rf = (annual_risk_free_rate / 252) * 100
    sharpe = (mean_r - daily_rf) / std_dev * math.sqrt(252)
    return round(sharpe, 4)


def _compute_max_drawdown(values: List[float]) -> float:
    """Maximum peak-to-trough drawdown as a positive percentage."""
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak else 0.0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def _compute_trade_stats(
    trades: List[Trade],
) -> Tuple[float, float, float, int, int]:
    """
    Compute win/loss statistics from SELL trades.

    Returns:
        (win_rate_pct, avg_win_pct, avg_loss_pct, winning_count, losing_count)
    """
    avg_costs: Dict[str, float] = {}
    shares_held: Dict[str, int] = {}
    wins: List[float] = []
    losses: List[float] = []

    for trade in sorted(trades, key=lambda t: t.timestamp):
        ticker = trade.ticker
        if trade.action == "BUY":
            prev_shares = shares_held.get(ticker, 0)
            prev_avg = avg_costs.get(ticker, 0.0)
            new_shares = prev_shares + trade.shares
            new_avg = (
                (prev_avg * prev_shares + trade.price * trade.shares) / new_shares
                if new_shares > 0
                else 0.0
            )
            avg_costs[ticker] = new_avg
            shares_held[ticker] = new_shares
        elif trade.action == "SELL":
            avg_cost = avg_costs.get(ticker, trade.price)
            pnl_pct = (
                ((trade.price / avg_cost) - 1) * 100 if avg_cost else 0.0
            )
            if pnl_pct >= 0:
                wins.append(pnl_pct)
            else:
                losses.append(pnl_pct)
            shares_held[ticker] = max(0, shares_held.get(ticker, 0) - trade.shares)

    total = len(wins) + len(losses)
    win_rate_pct = (len(wins) / total * 100) if total else 0.0
    avg_win_pct = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (sum(losses) / len(losses)) if losses else 0.0

    return win_rate_pct, avg_win_pct, avg_loss_pct, len(wins), len(losses)


def _compute_concentration(snapshot: PortfolioSnapshot, top_n: int = 3) -> float:
    """Weight of the top N holdings as a percentage of total portfolio value."""
    if not snapshot.positions or not snapshot.total_portfolio_value:
        return 0.0
    sorted_positions = sorted(
        snapshot.positions, key=lambda p: p.market_value, reverse=True
    )
    top_value = sum(p.market_value for p in sorted_positions[:top_n])
    return (top_value / snapshot.total_portfolio_value) * 100
