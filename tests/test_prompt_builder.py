"""Tests for prompt_builder.py"""

import pytest
from datetime import datetime

from src.data_ingestion.models import PersistentContext, PortfolioSnapshot, Position
from src.prompt_engine.prompt_builder import generate_prompt, TEMPLATES


def make_snapshot():
    positions = [
        Position(
            ticker="AAPL",
            company_name="Apple Inc.",
            shares=100,
            avg_cost_basis=150.0,
            current_price=175.0,
            market_value=17500.0,
            unrealized_pnl=2500.0,
            unrealized_pnl_pct=16.67,
            weight_pct=100.0,
        )
    ]
    return PortfolioSnapshot(
        timestamp=datetime(2024, 6, 1, 10, 0),
        total_portfolio_value=17500.0,
        cash=2500.0,
        invested_value=17500.0,
        cumulative_return_pct=16.67,
        positions=positions,
    )


def make_context():
    return PersistentContext(
        investment_strategy="Growth strategy",
        risk_tolerance="Moderate",
        investment_horizon="6 months",
        constraints=["No position > 35%", "Keep 5% cash"],
        rules=["Cut losses at -15%"],
    )


class TestGeneratePrompt:
    def test_trade_prompt_contains_ticker(self):
        prompt = generate_prompt(
            "trade", make_snapshot(), [], None, make_context(), "What should I do?"
        )
        assert "AAPL" in prompt

    def test_trade_prompt_contains_strategy(self):
        prompt = generate_prompt(
            "trade", make_snapshot(), [], None, make_context(), "What should I do?"
        )
        assert "Growth strategy" in prompt

    def test_trade_prompt_contains_question(self):
        question = "Should I buy more AAPL?"
        prompt = generate_prompt(
            "trade", make_snapshot(), [], None, make_context(), question
        )
        assert question in prompt

    def test_trade_prompt_contains_constraints(self):
        prompt = generate_prompt(
            "trade", make_snapshot(), [], None, make_context(), "Analyse"
        )
        assert "No position > 35%" in prompt

    def test_review_prompt_type(self):
        prompt = generate_prompt(
            "review", make_snapshot(), [], None, make_context(), "Review my portfolio"
        )
        assert "AAPL" in prompt
        assert "Review my portfolio" in prompt

    def test_risk_prompt_type(self):
        prompt = generate_prompt(
            "risk", make_snapshot(), [], None, make_context(), "Check risks"
        )
        assert "AAPL" in prompt

    def test_invalid_prompt_type_raises(self):
        with pytest.raises(KeyError):
            generate_prompt(
                "invalid_type", make_snapshot(), [], None, make_context(), "Q"
            )

    def test_templates_dict_has_required_keys(self):
        assert "trade" in TEMPLATES
        assert "review" in TEMPLATES
        assert "risk" in TEMPLATES

    def test_prompt_is_non_empty_string(self):
        prompt = generate_prompt(
            "trade", make_snapshot(), [], None, make_context(), "Q"
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 100
