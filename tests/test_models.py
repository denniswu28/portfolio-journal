"""Tests for Pydantic data models."""

import pytest
from datetime import datetime

from src.data_ingestion.models import (
    PersistentContext,
    PortfolioSnapshot,
    Position,
    RawPortfolioData,
    RawPosition,
    Trade,
)


class TestRawPosition:
    def test_ticker_normalized_to_uppercase(self):
        pos = RawPosition(
            ticker="baba",
            company_name="Alibaba",
            current_price=135.38,
            cost_basis_per_share=114.52,
            shares=500,
            market_value=67690.0,
        )
        assert pos.ticker == "BABA"

    def test_defaults(self):
        pos = RawPosition(
            ticker="AAPL",
            company_name="Apple",
            current_price=150.0,
            cost_basis_per_share=100.0,
            shares=10,
            market_value=1500.0,
        )
        assert pos.day_change == 0.0
        assert pos.day_change_pct == 0.0
        assert pos.gain_loss == 0.0

    def test_fractional_shares_supported(self):
        pos = RawPosition(
            ticker="AAPL",
            company_name="Apple",
            current_price=150.0,
            cost_basis_per_share=100.0,
            shares=2.543,
            market_value=381.45,
        )
        assert pos.shares == pytest.approx(2.543)


class TestTrade:
    def test_action_normalized_to_uppercase(self):
        trade = Trade(ticker="AAPL", action="buy", shares=10, price=150.0)
        assert trade.action == "BUY"

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError):
            Trade(ticker="AAPL", action="HOLD", shares=10, price=150.0)

    def test_total_value_computed(self):
        trade = Trade(ticker="AAPL", action="BUY", shares=100, price=150.0)
        assert trade.total_value == pytest.approx(15000.0)

    def test_id_auto_generated(self):
        trade = Trade(ticker="AAPL", action="BUY", shares=100, price=150.0)
        assert trade.id != ""
        assert "AAPL" in trade.id
        assert "BUY" in trade.id

    def test_fractional_trade_shares_supported(self):
        trade = Trade(ticker="AAPL", action="BUY", shares=0.5, price=150.0)
        assert trade.total_value == pytest.approx(75.0)


class TestPosition:
    def test_market_value_computed(self):
        pos = Position(
            ticker="AAPL",
            company_name="Apple",
            shares=100,
            avg_cost_basis=100.0,
            current_price=120.0,
        )
        assert pos.market_value == pytest.approx(12000.0)

    def test_unrealized_pnl_computed(self):
        pos = Position(
            ticker="AAPL",
            company_name="Apple",
            shares=100,
            avg_cost_basis=100.0,
            current_price=120.0,
        )
        assert pos.unrealized_pnl == pytest.approx(2000.0)
        assert pos.unrealized_pnl_pct == pytest.approx(20.0)


class TestPortfolioSnapshot:
    def test_invested_value_computed(self):
        positions = [
            Position(ticker="AAPL", company_name="Apple", shares=100,
                     avg_cost_basis=100.0, current_price=120.0),
            Position(ticker="GOOG", company_name="Google", shares=10,
                     avg_cost_basis=2000.0, current_price=2500.0),
        ]
        snap = PortfolioSnapshot(
            total_portfolio_value=37000.0,
            positions=positions,
        )
        assert snap.invested_value == pytest.approx(12000.0 + 25000.0)


class TestPersistentContext:
    def test_defaults(self):
        ctx = PersistentContext()
        assert ctx.risk_tolerance == "Moderate"
        assert ctx.investment_horizon == "6 months"
        assert ctx.constraints == []
        assert ctx.rules == []

    def test_custom_values(self):
        ctx = PersistentContext(
            investment_strategy="Growth",
            risk_tolerance="Aggressive",
            constraints=["No options"],
        )
        assert ctx.risk_tolerance == "Aggressive"
        assert "No options" in ctx.constraints
