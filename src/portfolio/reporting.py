"""Reporting helpers for portfolio metrics and spreadsheet exports."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

from src.data_ingestion.models import (
    AssetAllocationRow,
    FidelityAnalysisBundle,
    GeographicExposureRow,
    PerformanceMetrics,
    PeriodicReturnRow,
    PortfolioSnapshot,
    StyleExposureRow,
)


METRICS_FIELDS = [
    "as_of",
    "total_portfolio_value",
    "cash",
    "invested_value",
    "cumulative_return_pct",
    "annualized_return_pct",
    "annualized_volatility_pct",
    "sharpe_ratio",
    "calmar_ratio",
    "max_drawdown_pct",
    "max_drawdown_start",
    "max_drawdown_end",
    "max_drawdown_peak_value",
    "max_drawdown_trough_value",
    "win_rate_pct",
    "avg_win_pct",
    "avg_loss_pct",
    "concentration_top3_pct",
    "total_trades",
    "winning_trades",
    "losing_trades",
]

TIMESERIES_FIELDS = [
    "date",
    "portfolio_value",
    "period_return_pct",
    "cumulative_return_pct",
    "running_peak",
    "drawdown_pct",
]

SUPPLEMENTAL_SUMMARY_FIELDS = [
    "name",
    "current_value",
    "weight_pct",
]

PERIODIC_RETURN_FIELDS = [
    "period_end_date",
    "return_type",
    "account",
    "one_month_pct",
    "three_month_pct",
    "ytd_pct",
    "one_year_pct",
    "three_year_pct",
    "five_year_pct",
    "ten_year_pct",
    "life_pct",
    "life_start_date",
]

RETURN_SERIES_FIELDS = [
    "date",
    "source",
    "return_type",
    "account",
    "portfolio_value",
    "period_return_pct",
    "cumulative_return_pct",
    "fidelity_one_month_pct",
    "fidelity_ytd_pct",
    "fidelity_life_pct",
    "running_peak",
    "drawdown_pct",
]


def build_portfolio_timeseries(snapshots: List[PortfolioSnapshot]) -> List[dict[str, Any]]:
    """Build return and drawdown rows from portfolio snapshots."""
    if not snapshots:
        return []

    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    starting_value = ordered[0].total_portfolio_value
    previous_value: Optional[float] = None
    running_peak = 0.0
    rows: List[dict[str, Any]] = []

    for snapshot in ordered:
        value = snapshot.total_portfolio_value
        running_peak = max(running_peak, value)
        period_return_pct = (
            ((value / previous_value) - 1) * 100
            if previous_value
            else 0.0
        )
        cumulative_return_pct = (
            ((value / starting_value) - 1) * 100
            if starting_value
            else 0.0
        )
        drawdown_pct = (
            (running_peak - value) / running_peak * 100
            if running_peak
            else 0.0
        )
        rows.append(
            {
                "timestamp": snapshot.timestamp,
                "date": snapshot.timestamp.isoformat(sep=" ", timespec="seconds"),
                "portfolio_value": value,
                "period_return_pct": period_return_pct,
                "cumulative_return_pct": cumulative_return_pct,
                "running_peak": running_peak,
                "drawdown_pct": drawdown_pct,
            }
        )
        previous_value = value

    return rows


def select_latest_snapshot_per_day(
    snapshots: List[PortfolioSnapshot],
) -> List[PortfolioSnapshot]:
    """Return the latest saved snapshot for each calendar day."""
    latest_by_date: dict[date, PortfolioSnapshot] = {}
    for snapshot in sorted(snapshots, key=lambda item: item.timestamp):
        latest_by_date[snapshot.timestamp.date()] = snapshot
    return [latest_by_date[key] for key in sorted(latest_by_date)]


def metrics_to_row(
    metrics: PerformanceMetrics,
    latest_snapshot: Optional[PortfolioSnapshot] = None,
) -> dict[str, Any]:
    """Convert headline metrics into a one-row spreadsheet record."""
    return {
        "as_of": _format_value(latest_snapshot.timestamp if latest_snapshot else None),
        "total_portfolio_value": latest_snapshot.total_portfolio_value if latest_snapshot else "",
        "cash": latest_snapshot.cash if latest_snapshot else "",
        "invested_value": latest_snapshot.invested_value if latest_snapshot else "",
        "cumulative_return_pct": metrics.cumulative_return_pct,
        "annualized_return_pct": metrics.annualized_return_pct,
        "annualized_volatility_pct": metrics.annualized_volatility_pct,
        "sharpe_ratio": metrics.sharpe_ratio,
        "calmar_ratio": metrics.calmar_ratio,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "max_drawdown_start": _format_value(metrics.max_drawdown_start),
        "max_drawdown_end": _format_value(metrics.max_drawdown_end),
        "max_drawdown_peak_value": metrics.max_drawdown_peak_value,
        "max_drawdown_trough_value": metrics.max_drawdown_trough_value,
        "win_rate_pct": metrics.win_rate_pct,
        "avg_win_pct": metrics.avg_win_pct,
        "avg_loss_pct": metrics.avg_loss_pct,
        "concentration_top3_pct": metrics.concentration_top3_pct,
        "total_trades": metrics.total_trades,
        "winning_trades": metrics.winning_trades,
        "losing_trades": metrics.losing_trades,
    }


def write_metrics_summary(
    metrics: PerformanceMetrics,
    output_path: str | Path,
    latest_snapshot: Optional[PortfolioSnapshot] = None,
) -> Path:
    """Write headline metrics to a CSV file that opens cleanly in spreadsheets."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = metrics_to_row(metrics, latest_snapshot=latest_snapshot)
    _write_csv(path, METRICS_FIELDS, [row])
    return path


def write_portfolio_timeseries(
    snapshots: List[PortfolioSnapshot],
    output_path: str | Path,
) -> Path:
    """Write return and drawdown time series to a CSV file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_portfolio_timeseries(snapshots)
    _write_csv(path, TIMESERIES_FIELDS, rows)
    return path


def write_asset_allocation_summary(
    rows: List[AssetAllocationRow],
    output_path: str | Path,
) -> Path:
    """Write asset-class exposure summarized by current value."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, SUPPLEMENTAL_SUMMARY_FIELDS, summarize_asset_allocation(rows))
    return path


def write_geographic_exposure_summary(
    rows: List[GeographicExposureRow],
    output_path: str | Path,
) -> Path:
    """Write region exposure summarized by current value."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, SUPPLEMENTAL_SUMMARY_FIELDS, summarize_geographic_exposure(rows))
    return path


def write_style_exposure_summary(
    rows: List[StyleExposureRow],
    output_path: str | Path,
) -> Path:
    """Write style exposure summarized by current value."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, SUPPLEMENTAL_SUMMARY_FIELDS, summarize_style_exposure(rows))
    return path


def write_fidelity_periodic_returns(
    rows: List[PeriodicReturnRow],
    output_path: str | Path,
) -> Path:
    """Write Fidelity account-level periodic returns."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, PERIODIC_RETURN_FIELDS, [row.model_dump() for row in rows])
    return path


def write_return_series(
    snapshots: List[PortfolioSnapshot],
    periodic_returns: List[PeriodicReturnRow],
    output_path: str | Path,
) -> Path:
    """Write source-labeled snapshot and Fidelity return rows for graphing."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, RETURN_SERIES_FIELDS, build_return_series(snapshots, periodic_returns))
    return path


def build_return_series(
    snapshots: List[PortfolioSnapshot],
    periodic_returns: List[PeriodicReturnRow],
) -> List[dict[str, Any]]:
    """Build rows that keep snapshot-derived and Fidelity returns source-labeled."""
    rows: List[dict[str, Any]] = []
    for row in build_portfolio_timeseries(snapshots):
        rows.append(
            {
                "date": row["date"],
                "source": "snapshot",
                "return_type": "portfolio_value",
                "account": "",
                "portfolio_value": row["portfolio_value"],
                "period_return_pct": row["period_return_pct"],
                "cumulative_return_pct": row["cumulative_return_pct"],
                "running_peak": row["running_peak"],
                "drawdown_pct": row["drawdown_pct"],
            }
        )

    for row in sorted(periodic_returns, key=lambda item: (item.period_end_date, item.return_type)):
        if row.return_type != "time_weighted":
            continue
        rows.append(
            {
                "date": row.period_end_date.isoformat(),
                "source": "fidelity_periodic_returns",
                "return_type": row.return_type,
                "account": row.account,
                "portfolio_value": "",
                "period_return_pct": "",
                "cumulative_return_pct": row.life_pct,
                "fidelity_one_month_pct": row.one_month_pct,
                "fidelity_ytd_pct": row.ytd_pct,
                "fidelity_life_pct": row.life_pct,
                "running_peak": "",
                "drawdown_pct": "",
            }
        )

    return sorted(rows, key=lambda item: str(item["date"]))


def summarize_asset_allocation(rows: List[AssetAllocationRow], limit: int = 8) -> List[dict[str, Any]]:
    """Summarize asset allocation rows by asset class."""
    return _summarize_by_value(rows, lambda row: row.asset_class, lambda row: row.current_value, limit)


def summarize_geographic_exposure(rows: List[GeographicExposureRow], limit: int = 8) -> List[dict[str, Any]]:
    """Summarize geographic exposure rows by region."""
    return _summarize_by_value(rows, lambda row: row.region, lambda row: row.current_value, limit)


def summarize_country_exposure(rows: List[GeographicExposureRow], limit: int = 8) -> List[dict[str, Any]]:
    """Summarize geographic exposure rows by country."""
    return _summarize_by_value(rows, lambda row: row.country, lambda row: row.current_value, limit)


def summarize_style_exposure(rows: List[StyleExposureRow], limit: int = 9) -> List[dict[str, Any]]:
    """Summarize style exposure rows by style box."""
    return _summarize_by_value(rows, lambda row: row.style, lambda row: row.current_value, limit)


def latest_time_weighted_periodic_return(
    bundle: FidelityAnalysisBundle,
) -> Optional[PeriodicReturnRow]:
    """Return the latest time-weighted Fidelity periodic return row in a bundle."""
    rows = [row for row in bundle.periodic_returns if row.return_type == "time_weighted"]
    if not rows:
        return None
    return sorted(rows, key=lambda row: row.period_end_date)[-1]


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _format_value(row.get(field)) for field in fields})


def _format_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    return value


def _summarize_by_value(
    rows: Iterable[Any],
    name_getter,
    value_getter,
    limit: int,
) -> List[dict[str, Any]]:
    totals: dict[str, float] = {}
    for row in rows:
        name = name_getter(row) or "Unknown"
        totals[name] = totals.get(name, 0.0) + value_getter(row)

    total_value = sum(totals.values())
    summary = [
        {
            "name": name,
            "current_value": value,
            "weight_pct": (value / total_value * 100) if total_value else 0.0,
        }
        for name, value in totals.items()
    ]
    return sorted(summary, key=lambda row: row["current_value"], reverse=True)[:limit]