"""
reporting.py - Portfolio performance report renderer.

Generates human-readable text/markdown reports from a PortfolioSnapshot
and its associated PerformanceMetrics.  Reports are designed for CLI output
and for saving as markdown files under the configured reports directory.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from src.data_ingestion.models import PerformanceMetrics, PortfolioSnapshot


def build_report(
    snapshot: PortfolioSnapshot,
    metrics: PerformanceMetrics,
    as_markdown: bool = True,
) -> str:
    """
    Render a performance report from a snapshot and its computed metrics.

    Args:
        snapshot:     The latest PortfolioSnapshot.
        metrics:      PerformanceMetrics (computed over full snapshot history).
        as_markdown:  When True, adds markdown headers/bold; when False,
                      produces plain text with ASCII dividers (same content).

    Returns:
        A formatted multi-line string ready for print() or file write.
    """
    sep = "─" * 54
    header_char = "=" * 54

    def _h1(text: str) -> str:
        if as_markdown:
            return f"# {text}"
        return f"{header_char}\n{text}\n{header_char}"

    def _h2(text: str) -> str:
        if as_markdown:
            return f"\n## {text}"
        return f"\n{sep}\n{text}"

    def _bold(text: str) -> str:
        return f"**{text}**" if as_markdown else text

    def _na(value: Optional[float], fmt: str = ".2f", suffix: str = "%") -> str:
        if value is None:
            return "N/A (insufficient history)"
        return f"{value:{fmt}}{suffix}"

    as_of = snapshot.timestamp.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    lines.append(_h1(f"Portfolio Performance Report — {as_of}"))

    # ── Portfolio Summary ────────────────────────────────────────────────────
    lines.append(_h2("Portfolio Summary"))
    lines.append(f"{'Total Value':<30} ${snapshot.total_portfolio_value:>12,.2f}")
    lines.append(f"{'Cash':<30} ${snapshot.cash:>12,.2f}")
    lines.append(f"{'Invested':<30} ${snapshot.invested_value:>12,.2f}")
    lines.append(
        f"{'Cumulative Return':<30} {metrics.cumulative_return_pct:>+11.2f}%"
    )
    bench_label = f"Benchmark ({metrics.benchmark_ticker}) Return"
    lines.append(
        f"{bench_label:<30} {_na(metrics.benchmark_cumulative_return_pct)}"
    )

    # ── Risk-Adjusted Performance ────────────────────────────────────────────
    lines.append(_h2("Risk-Adjusted Performance"))
    lines.append(f"{'Sharpe Ratio (annualized)':<30} {_na(metrics.sharpe_ratio, fmt='.4f', suffix='')}")
    lines.append(
        f"{'Alpha vs ' + metrics.benchmark_ticker + ' (ann.)':<30} {_na(metrics.alpha_annualized_pct)}"
    )
    lines.append(
        f"{'Beta vs ' + metrics.benchmark_ticker:<30} {_na(metrics.beta, fmt='.4f', suffix='')}"
    )

    # ── Drawdown ─────────────────────────────────────────────────────────────
    lines.append(_h2("Drawdown"))
    lines.append(f"{'Current Drawdown':<30} {metrics.current_drawdown_pct:>11.2f}%")
    dd_date = f"  (trough {metrics.max_drawdown_date})" if metrics.max_drawdown_date else ""
    lines.append(f"{'Max Drawdown':<30} {metrics.max_drawdown_pct:>11.2f}%{dd_date}")

    # ── Trade Statistics ─────────────────────────────────────────────────────
    lines.append(_h2("Trade Statistics"))
    lines.append(f"{'Total Trades':<30} {metrics.total_trades:>12}")
    lines.append(f"{'Win Rate':<30} {metrics.win_rate_pct:>11.1f}%")
    lines.append(f"{'Avg Win':<30} {metrics.avg_win_pct:>+11.2f}%")
    lines.append(f"{'Avg Loss':<30} {metrics.avg_loss_pct:>+11.2f}%")

    # ── Concentration ────────────────────────────────────────────────────────
    lines.append(_h2("Concentration"))
    lines.append(
        f"{'Top-3 Holdings Weight':<30} {metrics.concentration_top3_pct:>11.1f}%"
    )
    if snapshot.positions:
        top3 = sorted(snapshot.positions, key=lambda p: p.market_value, reverse=True)[:3]
        for rank, pos in enumerate(top3, 1):
            lines.append(
                f"  {rank}. {pos.ticker:<8} {pos.weight_pct:>6.1f}%  ${pos.market_value:>10,.2f}"
            )

    # ── Footer ───────────────────────────────────────────────────────────────
    if not as_markdown:
        lines.append(f"\n{sep}")
    lines.append(
        f"\n_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
        if as_markdown
        else f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    return "\n".join(lines)


def save_report(
    report_text: str,
    reports_dir: str = "output/reports",
    as_of: Optional[datetime] = None,
    suffix: str = "md",
) -> Path:
    """
    Save *report_text* to a timestamped file in *reports_dir*.

    Args:
        report_text:  The rendered report string.
        reports_dir:  Directory to write the file (created if needed).
        as_of:        Timestamp for the filename (defaults to now).
        suffix:       File extension without dot (default "md").

    Returns:
        The Path of the saved file.
    """
    if as_of is None:
        as_of = datetime.now()

    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"report_{as_of.strftime('%Y%m%d_%H%M%S')}.{suffix}"
    path = out_dir / filename
    path.write_text(report_text, encoding="utf-8")
    return path
