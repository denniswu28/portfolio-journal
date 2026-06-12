"""Tests for portfolio-theory sleeve optimization."""

from datetime import datetime

import pandas as pd
import pytest

from src.portfolio.optimizer import (
    SleeveDefinition,
    build_rebalance_plan,
    current_values_by_sleeve,
    write_rebalance_csv,
    write_rebalance_markdown,
)
from tests.test_analytics import make_position, make_snapshot


def _prices_from_returns(returns):
    values = [100.0]
    for return_value in returns:
        values.append(values[-1] * (1.0 + return_value))
    return values


def test_equal_vol_plan_downweights_high_volatility_proxy(tmp_path):
    dates = pd.date_range("2024-01-05", periods=81, freq="W-FRI")
    low_vol_returns = [0.001, 0.002] * 40
    high_vol_returns = [0.06, -0.05] * 40
    price_history = pd.DataFrame(
        {
            "LOW": _prices_from_returns(low_vol_returns),
            "HIGH": _prices_from_returns(high_vol_returns),
        },
        index=dates,
    )
    sleeves = [
        SleeveDefinition(name="Stable growth", proxy="LOW", max_weight_pct=80.0),
        SleeveDefinition(name="Speculative growth", proxy="HIGH", max_weight_pct=80.0),
    ]

    plan = build_rebalance_plan(
        sleeves=sleeves,
        price_history=price_history,
        method="equal-vol",
        cash_target_pct=10.0,
        min_observations=20,
    )

    stable_row = next(row for row in plan["rows"] if row["sleeve"] == "Stable growth")
    speculative_row = next(row for row in plan["rows"] if row["sleeve"] == "Speculative growth")

    assert stable_row["target_weight_pct"] > speculative_row["target_weight_pct"]
    assert round(sum(row["target_weight_pct"] for row in plan["rows"]) + plan["cash_row"]["target_weight_pct"], 6) == 100.0

    csv_path = write_rebalance_csv(plan, tmp_path / "rebalance.csv")
    markdown_path = write_rebalance_markdown(plan, tmp_path / "rebalance.md")

    assert "equal_vol_weight_pct" in csv_path.read_text(encoding="utf-8")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    assert "Portfolio Theory Rebalance Plan" in markdown_text
    assert "Boist-Derived Growth Sleeves" in markdown_text


def test_current_values_map_snapshot_positions_to_sleeves():
    snapshot = make_snapshot(
        10000.0,
        ts=datetime(2026, 5, 6),
        positions=[
            make_position("NVDA", current_price=100.0, weight=25.0),
            make_position("IBB", current_price=100.0, weight=10.0),
            make_position("UNKNOWN", current_price=100.0, weight=5.0),
        ],
    )
    sleeves = [
        SleeveDefinition(name="AI semiconductors", proxy="SMH", holdings=("NVDA",)),
        SleeveDefinition(name="Healthtech", proxy="IBB"),
    ]

    values, unmapped = current_values_by_sleeve(snapshot, sleeves)

    assert values["AI semiconductors"] == 10000.0
    assert values["Healthtech"] == 10000.0
    assert [position["ticker"] for position in unmapped] == ["UNKNOWN"]


def test_rebalance_plan_rejects_invalid_cash_target():
    dates = pd.date_range("2024-01-05", periods=30, freq="W-FRI")
    price_history = pd.DataFrame({"AAA": range(100, 130)}, index=dates)
    sleeves = [SleeveDefinition(name="Core", proxy="AAA")]

    with pytest.raises(ValueError, match="Cash target"):
        build_rebalance_plan(
            sleeves=sleeves,
            price_history=price_history,
            cash_target_pct=100.0,
            min_observations=10,
        )