"""Tests for the deterministic rule engine and basket verdicts."""

from src.advisory.rules import (
    RuleThresholds,
    basket_action_candidates,
    evaluate_rules,
)
from src.data_ingestion.models import PortfolioSnapshot, Position


def _pos(ticker, market_value, pnl_pct=0.0, basket=None, weight=0.0):
    return Position(
        ticker=ticker, company_name=ticker, shares=1.0, avg_cost_basis=100.0,
        current_price=100.0, market_value=market_value, unrealized_pnl_pct=pnl_pct,
        weight_pct=weight, basket_name=basket,
    )


def _snapshot(positions, cash=0.0):
    total = sum(p.market_value for p in positions) + cash
    return PortfolioSnapshot(total_portfolio_value=total, cash=cash,
                             invested_value=sum(p.market_value for p in positions),
                             positions=positions)


def _categories(alerts):
    return {a.category for a in alerts}


def test_position_cap_breach_flags_action():
    snap = _snapshot([_pos("AAPL", 2000), _pos("MSFT", 100)], cash=0)
    alerts = evaluate_rules(snap)
    cap = [a for a in alerts if a.category == "position_cap"]
    assert cap and cap[0].severity == "ACTION" and cap[0].ticker == "AAPL"


def test_gold_exempt_from_cap():
    snap = _snapshot([_pos("GLDM", 5000), _pos("MSFT", 5000)])
    cap = [a for a in evaluate_rules(snap) if a.category == "position_cap"]
    assert all(a.ticker != "GLDM" for a in cap)


def test_take_profit_and_stop_loss_flags():
    snap = _snapshot([_pos("WIN", 500, pnl_pct=60.0), _pos("LOSE", 500, pnl_pct=-25.0)])
    cats = _categories(evaluate_rules(snap))
    assert "take_profit" in cats and "stop_loss" in cats


def test_cash_above_target_warns():
    snap = _snapshot([_pos("MSFT", 500)], cash=500)  # 50% cash
    cash_alerts = [a for a in evaluate_rules(snap) if a.category == "cash"]
    assert cash_alerts and cash_alerts[0].severity == "WARN"
    assert "above" in cash_alerts[0].title.lower()


def test_out_of_basket_listed():
    snap = _snapshot([_pos("FXAIX", 500, basket=None), _pos("MSFT", 500, basket="Tech")])
    orphan = [a for a in evaluate_rules(snap) if a.category == "out_of_basket"]
    assert orphan and "FXAIX" in orphan[0].detail


def test_band_drift_from_metrics():
    metrics = [
        {"basket": "AI Platform", "weight_pct": 16.0, "band_min_pct": 4, "band_max_pct": 12,
         "band_status": "ABOVE", "top_holding": "MSFT"},
        {"basket": "Memory", "weight_pct": 8.0, "band_min_pct": 8, "band_max_pct": 15,
         "band_status": "OK", "top_holding": "MU"},
    ]
    band = [a for a in evaluate_rules(_snapshot([_pos("MSFT", 100)]), basket_metrics=metrics)
            if a.category == "band"]
    assert len(band) == 1 and band[0].basket == "AI Platform"


def test_basket_action_candidates_verdicts():
    metrics = [
        {"basket": "AI Platform", "weight_pct": 16.0, "band_status": "ABOVE",
         "band_min_pct": 4, "band_max_pct": 12, "top_holding": "MSFT"},
        {"basket": "Memory", "weight_pct": 7.0, "band_status": "BELOW",
         "band_min_pct": 8, "band_max_pct": 15, "top_holding": "MU"},
        {"basket": "Gold", "weight_pct": 8.0, "band_status": "OK",
         "band_min_pct": 5, "band_max_pct": 10, "top_holding": "GLDM"},
    ]
    cands = basket_action_candidates(metrics, thesis_tickers={"MU"})
    by_basket = {c.basket: c for c in cands}
    assert by_basket["AI Platform"].verdict == "TRIM"
    assert by_basket["Memory"].verdict == "ADD"
    assert "Thesis-relevant" in by_basket["Memory"].note  # MU mentioned
    assert by_basket["Gold"].verdict == "HOLD"


def test_thresholds_are_configurable():
    snap = _snapshot([_pos("X", 1000, pnl_pct=30.0)])
    alerts = evaluate_rules(snap, thresholds=RuleThresholds(take_profit_pct=25.0))
    assert any(a.category == "take_profit" for a in alerts)
