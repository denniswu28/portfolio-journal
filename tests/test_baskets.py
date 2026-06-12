"""Tests for the first-class basket engine and the two portfolio-change methods."""

from datetime import datetime

import pytest

from src.data_ingestion.models import PortfolioSnapshot, Position
from src.portfolio.baskets import (
    METHOD_RECOMPOSE,
    METHOD_RESIZE,
    build_baskets,
    compute_basket_metrics,
    match_sleeve,
    recompose_basket,
    resize_basket,
    write_basket_order_plan,
)
from src.portfolio.optimizer import SleeveDefinition


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _pos(ticker, value, basket=None, price=100.0, pnl_pct=0.0):
    shares = value / price
    avg_cost = price / (1 + pnl_pct / 100) if (1 + pnl_pct / 100) else price
    return Position(
        ticker=ticker,
        company_name=f"{ticker} Inc",
        shares=shares,
        avg_cost_basis=avg_cost,
        current_price=price,
        market_value=value,
        unrealized_pnl=(price - avg_cost) * shares,
        unrealized_pnl_pct=pnl_pct,
        weight_pct=0.0,
        basket_name=basket,
    )


def _snapshot(positions, cash=0.0):
    invested = sum(p.market_value for p in positions)
    return PortfolioSnapshot(
        timestamp=datetime(2026, 6, 4, 14, 31),
        total_portfolio_value=invested + cash,
        cash=cash,
        invested_value=invested,
        positions=positions,
    )


def _sleeves():
    return [
        SleeveDefinition(
            name="Memory sleeve",
            proxy="MU",
            holdings=("MU", "SNDK", "WDC", "STX"),
            min_weight_pct=8.0,
            max_weight_pct=15.0,
        ),
        SleeveDefinition(
            name="Platform sleeve",
            proxy="IGV",
            holdings=("IGV", "MSFT", "GOOG", "AAPL", "QQQM"),
            min_weight_pct=4.0,
            max_weight_pct=12.0,
        ),
    ]


# ── BUILD / RECONCILE ────────────────────────────────────────────────────────

class TestBuildBaskets:
    def test_groups_by_basket_name_and_isolates_out_of_basket(self):
        snap = _snapshot(
            [
                _pos("MU", 500, "Memory"),
                _pos("SNDK", 500, "Memory"),
                _pos("IGV", 300, "Platform"),
                _pos("FXAIX", 800, None),
                _pos("SPAXX**", 1000, None),  # cash-like, excluded
            ],
            cash=1000.0,
        )
        view = build_baskets(snap, _sleeves())

        assert {b.name for b in view.baskets} == {"Memory", "Platform"}
        assert [p.ticker for p in view.out_of_basket] == ["FXAIX"]
        memory = view.get("Memory")
        assert memory.total_value == pytest.approx(1000.0)
        assert memory.component("MU").weight_in_basket_pct == pytest.approx(50.0)

    def test_matches_sleeve_by_overlap_and_sets_band(self):
        snap = _snapshot([_pos("MU", 500, "Memory"), _pos("SNDK", 500, "Memory")])
        view = build_baskets(snap, _sleeves())
        memory = view.get("Memory")
        assert memory.sleeve_name == "Memory sleeve"
        assert memory.band_min_pct == 8.0
        assert memory.band_max_pct == 15.0

    def test_band_status_above_below_ok(self):
        # Memory basket = 2000 of 10000 = 20% -> ABOVE the 15% ceiling.
        positions = [_pos("MU", 2000, "Memory")] + [_pos("FXAIX", 8000, None)]
        view = build_baskets(_snapshot(positions), _sleeves())
        assert view.get("Memory").band_status() == "ABOVE"

        # Memory = 500 of 10000 = 5% -> BELOW the 8% floor.
        positions = [_pos("MU", 500, "Memory")] + [_pos("FXAIX", 9500, None)]
        view = build_baskets(_snapshot(positions), _sleeves())
        assert view.get("Memory").band_status() == "BELOW"

        # Memory = 1000 of 10000 = 10% -> OK.
        positions = [_pos("MU", 1000, "Memory")] + [_pos("FXAIX", 9000, None)]
        view = build_baskets(_snapshot(positions), _sleeves())
        assert view.get("Memory").band_status() == "OK"

    def test_unmatched_basket_has_no_band(self):
        snap = _snapshot([_pos("ZZZZ", 500, "Mystery")])
        view = build_baskets(snap, _sleeves())
        mystery = view.get("Mystery")
        assert mystery.sleeve_name is None
        assert mystery.band_status() == "N/A"

    def test_match_sleeve_picks_highest_overlap(self):
        sleeves = _sleeves()
        assert match_sleeve(["MU", "SNDK"], sleeves).name == "Memory sleeve"
        assert match_sleeve(["IGV", "MSFT"], sleeves).name == "Platform sleeve"
        assert match_sleeve(["NOPE"], sleeves) is None


# ── METHOD A: RECOMPOSE ──────────────────────────────────────────────────────

class TestRecompose:
    def test_preserves_total_and_zero_net_cash(self):
        snap = _snapshot([_pos("MU", 600, "Memory"), _pos("SNDK", 400, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = recompose_basket(
            basket, {"MU": 50, "SNDK": 50}, portfolio_value=1000.0, min_trade_dollars=0.0
        )
        assert plan.method == METHOD_RECOMPOSE
        assert plan.target_total == pytest.approx(1000.0)
        assert plan.net_cash == pytest.approx(0.0, abs=1e-6)

    def test_shift_generates_buy_and_sell(self):
        snap = _snapshot([_pos("MU", 600, "Memory"), _pos("SNDK", 400, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = recompose_basket(
            basket, {"MU": 40, "SNDK": 60}, portfolio_value=1000.0, min_trade_dollars=0.0
        )
        by_ticker = {o.ticker: o for o in plan.orders}
        assert by_ticker["MU"].action == "SELL"
        assert by_ticker["MU"].dollars == pytest.approx(200.0)
        assert by_ticker["SNDK"].action == "BUY"
        assert by_ticker["SNDK"].dollars == pytest.approx(200.0)

    def test_add_new_ticker_requires_price(self):
        snap = _snapshot([_pos("IGV", 1000, "Platform")])
        basket = build_baskets(snap, _sleeves()).get("Platform")
        plan = recompose_basket(
            basket,
            {"IGV": 80, "MSFT": 20},
            portfolio_value=1000.0,
            prices={"MSFT": 400.0},
            min_trade_dollars=0.0,
        )
        msft = next(o for o in plan.orders if o.ticker == "MSFT")
        assert msft.action == "BUY"
        assert msft.dollars == pytest.approx(200.0)
        assert msft.shares == pytest.approx(0.5)

    def test_add_without_price_is_skipped_with_note(self):
        snap = _snapshot([_pos("IGV", 1000, "Platform")])
        basket = build_baskets(snap, _sleeves()).get("Platform")
        plan = recompose_basket(
            basket, {"IGV": 80, "MSFT": 20}, portfolio_value=1000.0, min_trade_dollars=0.0
        )
        assert all(o.ticker != "MSFT" for o in plan.orders)
        assert any("MSFT" in n for n in plan.notes)

    def test_new_total_override_changes_basket_size(self):
        snap = _snapshot([_pos("MU", 1000, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = recompose_basket(
            basket, {"MU": 100}, portfolio_value=2000.0, new_total=1200.0, min_trade_dollars=0.0
        )
        assert plan.target_total == pytest.approx(1200.0)
        mu = next(o for o in plan.orders if o.ticker == "MU")
        assert mu.action == "BUY"
        assert mu.dollars == pytest.approx(200.0)

    def test_min_trade_suppresses_small_orders(self):
        snap = _snapshot([_pos("MU", 510, "Memory"), _pos("SNDK", 490, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = recompose_basket(
            basket, {"MU": 50, "SNDK": 50}, portfolio_value=1000.0, min_trade_dollars=25.0
        )
        assert all(o.action == "HOLD" for o in plan.orders)


# ── METHOD B: RESIZE ─────────────────────────────────────────────────────────

class TestResize:
    def test_delta_removes_dollars_all_sells(self):
        snap = _snapshot([_pos("MU", 600, "Memory"), _pos("SNDK", 400, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = resize_basket(basket, portfolio_value=1000.0, delta_dollars=-300.0, min_trade_dollars=0.0)
        assert plan.method == METHOD_RESIZE
        assert plan.target_total == pytest.approx(700.0)
        assert all(o.action == "SELL" for o in plan.orders)
        assert plan.net_cash == pytest.approx(300.0)

    def test_resize_preserves_component_ratios(self):
        snap = _snapshot([_pos("MU", 600, "Memory"), _pos("SNDK", 400, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = resize_basket(basket, portfolio_value=1000.0, new_total=500.0, min_trade_dollars=0.0)
        by_ticker = {o.ticker: o for o in plan.orders}
        # 60/40 split preserved: targets 300 / 200.
        assert by_ticker["MU"].target_dollars == pytest.approx(300.0)
        assert by_ticker["SNDK"].target_dollars == pytest.approx(200.0)

    def test_new_total_grows_basket_all_buys(self):
        snap = _snapshot([_pos("MU", 500, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        plan = resize_basket(basket, portfolio_value=1000.0, new_total=800.0, min_trade_dollars=0.0)
        assert all(o.action == "BUY" for o in plan.orders)
        assert plan.net_cash == pytest.approx(-300.0)

    def test_requires_exactly_one_of_new_total_or_delta(self):
        snap = _snapshot([_pos("MU", 500, "Memory")])
        basket = build_baskets(snap, _sleeves()).get("Memory")
        with pytest.raises(ValueError):
            resize_basket(basket, portfolio_value=1000.0)
        with pytest.raises(ValueError):
            resize_basket(basket, portfolio_value=1000.0, new_total=800.0, delta_dollars=100.0)


# ── METRICS / MARKDOWN ───────────────────────────────────────────────────────

class TestMetricsAndMarkdown:
    def test_compute_basket_metrics_rows(self):
        snap = _snapshot(
            [_pos("MU", 600, "Memory", pnl_pct=20.0), _pos("SNDK", 400, "Memory")],
            cash=0.0,
        )
        view = build_baskets(snap, _sleeves())
        rows = compute_basket_metrics(view)
        assert len(rows) == 1
        row = rows[0]
        assert row["basket"] == "Memory"
        assert row["weight_pct"] == pytest.approx(100.0)
        assert row["holdings"] == 2
        assert row["top_holding"] == "MU"

    def test_write_order_plan_markdown(self, tmp_path):
        snap = _snapshot([_pos("MU", 600, "Memory"), _pos("SNDK", 400, "Memory")], cash=200.0)
        view = build_baskets(snap, _sleeves())
        basket = view.get("Memory")
        plan = resize_basket(basket, portfolio_value=view.portfolio_value, delta_dollars=-200.0, min_trade_dollars=0.0)
        out = write_basket_order_plan([plan], view, tmp_path / "plan.md", title="Test Plan")
        text = out.read_text(encoding="utf-8")
        assert "Test Plan" in text
        assert "Method B - resize" in text
        assert "Net cash reconciliation" in text
        assert "| SELL | MU |" in text
