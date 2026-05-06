"""Tests for portfolio analytics."""

import pytest
from datetime import datetime, timedelta

from src.data_ingestion.models import PerformanceMetrics, PortfolioSnapshot, Position, Trade
from src.portfolio.analytics import (
    calculate_avg_cost_basis,
    compute_metrics,
    realized_pnl,
    total_pnl,
    unrealized_pnl,
    _compute_max_drawdown,
    _compute_sharpe,
)


def make_position(ticker="AAPL", shares=100, avg_cost=100.0, current_price=120.0, weight=100.0):
    return Position(
        ticker=ticker,
        company_name=f"{ticker} Corp",
        shares=shares,
        avg_cost_basis=avg_cost,
        current_price=current_price,
        market_value=shares * current_price,
        unrealized_pnl=(current_price - avg_cost) * shares,
        unrealized_pnl_pct=((current_price / avg_cost) - 1) * 100,
        weight_pct=weight,
    )


def make_snapshot(value: float, ts: datetime = None, positions=None):
    ts = ts or datetime.now()
    pos = positions or [make_position(current_price=value / 100)]
    return PortfolioSnapshot(
        timestamp=ts,
        total_portfolio_value=value,
        cash=0.0,
        invested_value=value,
        positions=pos,
    )


def make_trade(ticker, action, shares, price, ts=None):
    ts = ts or datetime.now()
    return Trade(ticker=ticker, action=action, shares=shares, price=price, timestamp=ts)


class TestAvgCostBasis:
    def test_single_buy(self):
        trades = [make_trade("AAPL", "BUY", 100, 100.0)]
        assert calculate_avg_cost_basis(trades, "AAPL") == pytest.approx(100.0)

    def test_two_buys(self):
        trades = [
            make_trade("AAPL", "BUY", 100, 100.0),
            make_trade("AAPL", "BUY", 100, 200.0),
        ]
        assert calculate_avg_cost_basis(trades, "AAPL") == pytest.approx(150.0)

    def test_buy_then_partial_sell(self):
        trades = [
            make_trade("AAPL", "BUY", 100, 100.0),
            make_trade("AAPL", "SELL", 50, 120.0),
        ]
        assert calculate_avg_cost_basis(trades, "AAPL") == pytest.approx(100.0)

    def test_no_trades_returns_zero(self):
        assert calculate_avg_cost_basis([], "AAPL") == 0.0

    def test_different_ticker_ignored(self):
        trades = [make_trade("GOOG", "BUY", 100, 150.0)]
        assert calculate_avg_cost_basis(trades, "AAPL") == 0.0


class TestUnrealizedPnl:
    def test_positive_pnl(self):
        pos = make_position(avg_cost=100.0, current_price=120.0, shares=100)
        result = unrealized_pnl(pos)
        assert result["dollars"] == pytest.approx(2000.0)
        assert result["percent"] == pytest.approx(20.0)

    def test_negative_pnl(self):
        pos = make_position(avg_cost=100.0, current_price=80.0, shares=100)
        result = unrealized_pnl(pos)
        assert result["dollars"] == pytest.approx(-2000.0)
        assert result["percent"] == pytest.approx(-20.0)

    def test_zero_pnl(self):
        pos = make_position(avg_cost=100.0, current_price=100.0, shares=100)
        result = unrealized_pnl(pos)
        assert result["dollars"] == pytest.approx(0.0)
        assert result["percent"] == pytest.approx(0.0)


class TestRealizedPnl:
    def test_winning_sell(self):
        trades = [
            make_trade("AAPL", "BUY", 100, 100.0),
            make_trade("AAPL", "SELL", 100, 150.0),
        ]
        assert realized_pnl(trades) == pytest.approx(5000.0)

    def test_losing_sell(self):
        trades = [
            make_trade("AAPL", "BUY", 100, 100.0),
            make_trade("AAPL", "SELL", 100, 80.0),
        ]
        assert realized_pnl(trades) == pytest.approx(-2000.0)

    def test_no_sells(self):
        trades = [make_trade("AAPL", "BUY", 100, 100.0)]
        assert realized_pnl(trades) == pytest.approx(0.0)

    def test_empty_trades(self):
        assert realized_pnl([]) == pytest.approx(0.0)


class TestComputeMetrics:
    def test_empty_snapshots(self):
        metrics = compute_metrics([])
        assert isinstance(metrics, PerformanceMetrics)
        assert metrics.cumulative_return_pct == 0.0

    def test_single_snapshot(self):
        metrics = compute_metrics([make_snapshot(100000.0)])
        assert metrics.cumulative_return_pct == 0.0

    def test_positive_return(self):
        base = datetime(2024, 1, 1)
        snapshots = [
            make_snapshot(100000.0, ts=base),
            make_snapshot(110000.0, ts=base + timedelta(days=1)),
        ]
        metrics = compute_metrics(snapshots)
        assert metrics.cumulative_return_pct == pytest.approx(10.0)

    def test_negative_return(self):
        base = datetime(2024, 1, 1)
        snapshots = [
            make_snapshot(100000.0, ts=base),
            make_snapshot(90000.0, ts=base + timedelta(days=1)),
        ]
        metrics = compute_metrics(snapshots)
        assert metrics.cumulative_return_pct == pytest.approx(-10.0)

    def test_max_drawdown(self):
        values = [100, 120, 80, 90]
        dd = _compute_max_drawdown(values)
        # Peak=120, trough=80 → drawdown = (120-80)/120 * 100 = 33.33%
        assert dd == pytest.approx(33.3333, rel=1e-3)

    def test_sharpe_returns_none_for_single_return(self):
        sharpe = _compute_sharpe([5.0])
        assert sharpe is None

    def test_sharpe_computed(self):
        returns = [1.0, -0.5, 2.0, -1.0, 1.5, 0.5, -0.5, 1.0]
        sharpe = _compute_sharpe(returns)
        assert sharpe is not None
        assert isinstance(sharpe, float)

    def test_extended_risk_metrics(self):
        base = datetime(2024, 1, 1)
        snapshots = [
            make_snapshot(100000.0, ts=base),
            make_snapshot(120000.0, ts=base + timedelta(days=1)),
            make_snapshot(90000.0, ts=base + timedelta(days=2)),
            make_snapshot(110000.0, ts=base + timedelta(days=3)),
        ]

        metrics = compute_metrics(snapshots)

        assert metrics.annualized_return_pct != 0.0
        assert metrics.annualized_volatility_pct > 0.0
        assert metrics.calmar_ratio is not None
        assert metrics.max_drawdown_pct == pytest.approx(25.0)
        assert metrics.max_drawdown_start == base + timedelta(days=1)
        assert metrics.max_drawdown_end == base + timedelta(days=2)
