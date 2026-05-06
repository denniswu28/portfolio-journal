"""Matplotlib chart generation for portfolio reports."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from src.data_ingestion.models import (
    AssetAllocationRow,
    GeographicExposureRow,
    PeriodicReturnRow,
    PortfolioSnapshot,
    StyleExposureRow,
)
from src.portfolio.reporting import (
    build_portfolio_timeseries,
    summarize_asset_allocation,
    summarize_geographic_exposure,
    summarize_style_exposure,
)


def plot_return_drawdown(
    snapshots: List[PortfolioSnapshot],
    output_path: str | Path,
    show: bool = False,
    periodic_returns: Optional[List[PeriodicReturnRow]] = None,
) -> Path:
    """Save a combined cumulative return and drawdown analysis chart."""
    rows = build_portfolio_timeseries(snapshots)
    fidelity_rows = [
        row
        for row in (periodic_returns or [])
        if row.return_type == "time_weighted" and row.life_pct is not None
    ]
    if not rows and not fidelity_rows:
        raise ValueError("Need at least one snapshot or Fidelity return point to plot returns.")

    fig, (return_ax, drawdown_ax) = plt.subplots(
        2,
        1,
        figsize=(11, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [2, 1]},
    )

    if rows:
        dates = [row["timestamp"] for row in rows]
        cumulative_returns = [row["cumulative_return_pct"] for row in rows]
        drawdowns = [-row["drawdown_pct"] for row in rows]
        worst_index = max(range(len(rows)), key=lambda i: rows[i]["drawdown_pct"])
        return_ax.plot(dates, cumulative_returns, color="#2563eb", linewidth=2.2, marker="o", label="Snapshot value")
    if fidelity_rows:
        return_ax.scatter(
            [row.period_end_date for row in fidelity_rows],
            [row.life_pct for row in fidelity_rows],
            color="#7c3aed",
            marker="D",
            s=52,
            label="Fidelity life TWR",
            zorder=4,
        )
    return_ax.axhline(0, color="#6b7280", linewidth=0.8, linestyle="--")
    return_ax.set_title("Portfolio Return and Drawdown")
    return_ax.set_ylabel("Cumulative return (%)")
    return_ax.grid(True, alpha=0.25)
    if rows and fidelity_rows:
        return_ax.legend(loc="best")

    if rows:
        drawdown_ax.fill_between(dates, drawdowns, 0, color="#dc2626", alpha=0.25)
        drawdown_ax.plot(dates, drawdowns, color="#b91c1c", linewidth=1.6)
    drawdown_ax.axhline(0, color="#6b7280", linewidth=0.8)
    drawdown_ax.set_ylabel("Drawdown (%)")
    drawdown_ax.grid(True, alpha=0.25)

    worst_drawdown = rows[worst_index]["drawdown_pct"] if rows else 0.0
    if rows and worst_drawdown > 0:
        drawdown_ax.scatter(
            dates[worst_index],
            drawdowns[worst_index],
            color="#991b1b",
            zorder=3,
        )
        drawdown_ax.annotate(
            f"Max DD: -{worst_drawdown:.2f}%",
            xy=(dates[worst_index], drawdowns[worst_index]),
            xytext=(8, -18),
            textcoords="offset points",
            fontsize=9,
            color="#991b1b",
        )
    elif not rows:
        drawdown_ax.text(
            0.5,
            0.5,
            "Drawdown requires portfolio value snapshots",
            ha="center",
            va="center",
            transform=drawdown_ax.transAxes,
            color="#6b7280",
        )

    fig.autofmt_xdate()
    return _save_figure(fig, output_path, show=show)


def plot_unrealized_pnl(
    snapshot: PortfolioSnapshot,
    output_path: str | Path,
    show: bool = False,
) -> Path:
    """Save a horizontal bar chart of latest unrealized P&L percentages."""
    if not snapshot.positions:
        raise ValueError("Need at least one position to plot unrealized P&L.")

    positions = sorted(snapshot.positions, key=lambda p: p.unrealized_pnl_pct)
    tickers = [p.ticker for p in positions]
    pnl_values = [p.unrealized_pnl_pct for p in positions]
    colors = ["#16a34a" if value >= 0 else "#dc2626" for value in pnl_values]

    fig_height = max(5, len(positions) * 0.35)
    fig, ax = plt.subplots(figsize=(11, fig_height))
    ax.barh(tickers, pnl_values, color=colors)
    ax.axvline(0, color="#374151", linewidth=0.9)
    ax.set_title("Latest Unrealized P&L by Position")
    ax.set_xlabel("Unrealized P&L (%)")
    ax.grid(axis="x", alpha=0.25)

    return _save_figure(fig, output_path, show=show)


def plot_position_weights(
    snapshot: PortfolioSnapshot,
    output_path: str | Path,
    show: bool = False,
    top_n: int = 12,
) -> Path:
    """Save a concentration chart of latest position weights."""
    if not snapshot.positions:
        raise ValueError("Need at least one position to plot position weights.")

    positions = sorted(snapshot.positions, key=lambda p: p.weight_pct, reverse=True)
    displayed = positions[:top_n]
    other_weight = sum(p.weight_pct for p in positions[top_n:])
    labels = [p.ticker for p in displayed]
    weights = [p.weight_pct for p in displayed]
    if other_weight > 0:
        labels.append("Other")
        weights.append(other_weight)

    fig_height = max(5, len(labels) * 0.35)
    fig, ax = plt.subplots(figsize=(11, fig_height))
    ax.barh(labels[::-1], weights[::-1], color="#0f766e")
    ax.set_title("Latest Position Weights")
    ax.set_xlabel("Portfolio weight (%)")
    ax.grid(axis="x", alpha=0.25)

    return _save_figure(fig, output_path, show=show)


def plot_asset_allocation(
    rows: List[AssetAllocationRow],
    output_path: str | Path,
    show: bool = False,
) -> Path:
    """Save an asset-class exposure chart."""
    return _plot_summary_bar(
        summarize_asset_allocation(rows),
        "Asset Class Exposure",
        output_path,
        show=show,
        color="#2563eb",
    )


def plot_geographic_exposure(
    rows: List[GeographicExposureRow],
    output_path: str | Path,
    show: bool = False,
) -> Path:
    """Save a geographic exposure chart by region."""
    return _plot_summary_bar(
        summarize_geographic_exposure(rows),
        "Geographic Exposure by Region",
        output_path,
        show=show,
        color="#0f766e",
    )


def plot_style_exposure(
    rows: List[StyleExposureRow],
    output_path: str | Path,
    show: bool = False,
) -> Path:
    """Save a style-box exposure chart."""
    return _plot_summary_bar(
        summarize_style_exposure(rows),
        "Style Exposure",
        output_path,
        show=show,
        color="#9333ea",
    )


def _plot_summary_bar(
    summary: list[dict[str, object]],
    title: str,
    output_path: str | Path,
    show: bool = False,
    color: str = "#2563eb",
) -> Path:
    if not summary:
        raise ValueError(f"Need at least one row to plot {title.lower()}.")

    labels = [str(row["name"]) for row in summary]
    weights = [float(row["weight_pct"]) for row in summary]
    fig_height = max(4.5, len(labels) * 0.42)
    fig, ax = plt.subplots(figsize=(11, fig_height))
    ax.barh(labels[::-1], weights[::-1], color=color)
    ax.set_title(title)
    ax.set_xlabel("Current value weight (%)")
    ax.grid(axis="x", alpha=0.25)
    return _save_figure(fig, output_path, show=show)


def _save_figure(fig, output_path: str | Path, show: bool = False) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    return path