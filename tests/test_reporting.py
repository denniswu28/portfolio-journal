"""Tests for the portfolio reporting module."""

import pytest
from datetime import datetime
from pathlib import Path

from src.data_ingestion.models import PerformanceMetrics, PortfolioSnapshot, Position
from src.portfolio.reporting import build_report, save_report


def _make_position(ticker="AAPL", shares=100, avg_cost=100.0, price=120.0, weight=60.0):
    return Position(
        ticker=ticker,
        company_name=f"{ticker} Corp",
        shares=shares,
        avg_cost_basis=avg_cost,
        current_price=price,
        market_value=shares * price,
        unrealized_pnl=(price - avg_cost) * shares,
        unrealized_pnl_pct=((price / avg_cost) - 1) * 100,
        weight_pct=weight,
    )


def _make_snapshot(value=100_000.0, cash=5_000.0):
    return PortfolioSnapshot(
        timestamp=datetime(2024, 4, 1, 9, 30),
        total_portfolio_value=value,
        cash=cash,
        invested_value=value - cash,
        positions=[
            _make_position("AAPL", shares=300, weight=36.0),
            _make_position("MSFT", shares=200, price=250.0, weight=50.0),
            _make_position("NVDA", shares=50, price=800.0, weight=40.0),
        ],
    )


def _full_metrics():
    return PerformanceMetrics(
        cumulative_return_pct=12.5,
        sharpe_ratio=1.42,
        max_drawdown_pct=8.3,
        max_drawdown_date="2024-02-10",
        current_drawdown_pct=2.1,
        win_rate_pct=60.0,
        avg_win_pct=5.5,
        avg_loss_pct=-3.2,
        concentration_top3_pct=65.0,
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        benchmark_ticker="SPY",
        benchmark_cumulative_return_pct=7.8,
        alpha_annualized_pct=3.2,
        beta=0.88,
    )


class TestBuildReport:
    def test_contains_as_of_date(self):
        snap = _make_snapshot()
        report = build_report(snap, _full_metrics())
        assert "2024-04-01" in report

    def test_contains_cumulative_return(self):
        snap = _make_snapshot()
        report = build_report(snap, _full_metrics())
        assert "12.50" in report

    def test_contains_sharpe(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "1.4200" in report

    def test_contains_alpha(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "3.20" in report

    def test_contains_beta(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "0.8800" in report

    def test_contains_max_drawdown(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "8.30" in report
        assert "2024-02-10" in report

    def test_contains_current_drawdown(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "2.10" in report

    def test_contains_benchmark_ticker(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "SPY" in report

    def test_na_when_metrics_none(self):
        metrics = PerformanceMetrics()  # all optional fields are None
        report = build_report(_make_snapshot(), metrics)
        assert "N/A" in report

    def test_plain_text_mode(self):
        report = build_report(_make_snapshot(), _full_metrics(), as_markdown=False)
        # Should not contain markdown headers
        assert "# " not in report
        assert "##" not in report

    def test_markdown_mode_has_headers(self):
        report = build_report(_make_snapshot(), _full_metrics(), as_markdown=True)
        assert "# " in report or "##" in report

    def test_top3_holdings_listed(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "AAPL" in report
        assert "MSFT" in report

    def test_benchmark_return_displayed(self):
        report = build_report(_make_snapshot(), _full_metrics())
        assert "7.80" in report


class TestSaveReport:
    def test_file_created(self, tmp_path):
        snap = _make_snapshot()
        report = build_report(snap, _full_metrics())
        saved = save_report(report, reports_dir=str(tmp_path))
        assert saved.exists()
        assert saved.suffix == ".md"

    def test_file_content_matches(self, tmp_path):
        snap = _make_snapshot()
        report = build_report(snap, _full_metrics())
        saved = save_report(report, reports_dir=str(tmp_path))
        assert saved.read_text(encoding="utf-8") == report

    def test_directory_created_if_missing(self, tmp_path):
        sub = tmp_path / "deep" / "reports"
        report = build_report(_make_snapshot(), _full_metrics())
        saved = save_report(report, reports_dir=str(sub))
        assert saved.exists()

    def test_custom_suffix(self, tmp_path):
        report = build_report(_make_snapshot(), _full_metrics())
        saved = save_report(report, reports_dir=str(tmp_path), suffix="txt")
        assert saved.suffix == ".txt"

    def test_filename_includes_timestamp(self, tmp_path):
        as_of = datetime(2024, 4, 1, 9, 30, 0)
        report = build_report(_make_snapshot(), _full_metrics())
        saved = save_report(report, reports_dir=str(tmp_path), as_of=as_of)
        assert "20240401_093000" in saved.name
