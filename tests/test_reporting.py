"""Tests for portfolio report exports and plots."""

from datetime import date, datetime, timedelta

from src.data_ingestion.models import AssetAllocationRow, PeriodicReturnRow
from src.portfolio.analytics import compute_metrics
from src.portfolio.plots import (
    plot_asset_allocation,
    plot_position_weights,
    plot_return_drawdown,
    plot_unrealized_pnl,
)
from src.portfolio.reporting import (
    build_return_series,
    select_latest_snapshot_per_day,
    write_asset_allocation_summary,
    write_metrics_summary,
    write_portfolio_timeseries,
    write_return_series,
)
from tests.test_analytics import make_position, make_snapshot


def _make_snapshots():
    base = datetime(2026, 5, 1)
    return [
        make_snapshot(
            100000.0,
            ts=base,
            positions=[
                make_position("AAPL", current_price=120.0, weight=60.0),
                make_position("MSFT", current_price=90.0, weight=40.0),
            ],
        ),
        make_snapshot(
            110000.0,
            ts=base + timedelta(days=1),
            positions=[
                make_position("AAPL", current_price=130.0, weight=62.0),
                make_position("MSFT", current_price=95.0, weight=38.0),
            ],
        ),
        make_snapshot(
            95000.0,
            ts=base + timedelta(days=2),
            positions=[
                make_position("AAPL", current_price=100.0, weight=55.0),
                make_position("MSFT", current_price=86.0, weight=45.0),
            ],
        ),
    ]


def test_report_spreadsheets_include_core_metrics(tmp_path):
    snapshots = _make_snapshots()
    metrics = compute_metrics(snapshots)

    metrics_path = write_metrics_summary(
        metrics,
        tmp_path / "metrics_summary.csv",
        latest_snapshot=snapshots[-1],
    )
    timeseries_path = write_portfolio_timeseries(
        snapshots,
        tmp_path / "portfolio_timeseries.csv",
    )

    metrics_text = metrics_path.read_text(encoding="utf-8")
    timeseries_text = timeseries_path.read_text(encoding="utf-8")

    assert "sharpe_ratio" in metrics_text
    assert "calmar_ratio" in metrics_text
    assert "max_drawdown_pct" in metrics_text
    assert "annualized_return_pct" in metrics_text
    assert "portfolio_value" in timeseries_text
    assert "drawdown_pct" in timeseries_text


def test_report_plots_are_written(tmp_path):
    snapshots = _make_snapshots()
    latest_snapshot = snapshots[-1]

    paths = [
        plot_return_drawdown(snapshots, tmp_path / "return_drawdown.png"),
        plot_unrealized_pnl(latest_snapshot, tmp_path / "unrealized_pnl.png"),
        plot_position_weights(latest_snapshot, tmp_path / "position_weights.png"),
    ]

    for path in paths:
        assert path.exists()
        assert path.stat().st_size > 0


def test_select_latest_snapshot_per_day():
    base = datetime(2026, 5, 1)
    snapshots = [
        make_snapshot(100000.0, ts=base.replace(hour=9)),
        make_snapshot(101000.0, ts=base.replace(hour=16)),
        make_snapshot(103000.0, ts=base + timedelta(days=1)),
    ]

    daily_snapshots = select_latest_snapshot_per_day(snapshots)

    assert [snapshot.total_portfolio_value for snapshot in daily_snapshots] == [
        101000.0,
        103000.0,
    ]


def test_supplemental_report_outputs_are_written(tmp_path):
    rows = [
        AssetAllocationRow(symbol="AAPL", asset_class="Domestic Stock", current_value=900.0),
        AssetAllocationRow(symbol="SPAXX", asset_class="Short Term", current_value=100.0),
    ]

    summary_path = write_asset_allocation_summary(rows, tmp_path / "asset_allocation_summary.csv")
    plot_path = plot_asset_allocation(rows, tmp_path / "asset_allocation.png")

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Domestic Stock" in summary_text
    assert "weight_pct" in summary_text
    assert plot_path.exists()
    assert plot_path.stat().st_size > 0


def test_return_series_keeps_fidelity_rows_source_labeled(tmp_path):
    snapshots = _make_snapshots()[:2]
    periodic_rows = [
        PeriodicReturnRow(
            period_end_date=date(2026, 4, 30),
            return_type="time_weighted",
            account="Cash Management Z1",
            one_month_pct=10.46,
            ytd_pct=9.07,
            life_pct=7.75,
        )
    ]

    rows = build_return_series(snapshots, periodic_rows)
    path = write_return_series(snapshots, periodic_rows, tmp_path / "return_series.csv")

    assert any(row["source"] == "snapshot" for row in rows)
    assert any(row["source"] == "fidelity_periodic_returns" for row in rows)
    assert "fidelity_periodic_returns" in path.read_text(encoding="utf-8")