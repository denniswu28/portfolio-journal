"""
backtest_baskets.py — 1-month basket performance backtest.

Usage:
    python backtest_baskets.py [--csv PATH] [--days N]

Reads the current Fidelity positions CSV, groups holdings by basket,
fetches N days (default 31) of price history from Yahoo Finance, and
reports the dollar-weighted 1-month return for each basket and position.

Methodology:
  - "Current" composition is used as the holding-period proxy.
  - Returns are price-only (no dividends) using adjusted daily closes.
  - Basket return = sum(position_weight * position_return) for positions
    with available price data.  Positions with no yfinance data are noted
    but excluded from the weighted average.
  - SPAXX and Pending Activity rows are always excluded.

Output:
  - Console table
  - output/reports/basket_backtest_1m_YYYYMMDD.md
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import os

import click
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

# ── CONSTANTS ─────────────────────────────────────────────────────────────────

DEFAULT_CSV = "data/portfolio_snapshots/Portfolio_Positions_May-08-2026.csv"
SKIP_SYMBOLS = {"SPAXX**", "SPAXX", "Pending activity"}
SKIP_BASKETS: set[str] = set()          # leave empty to include outside holdings
OUTSIDE_LABEL = "Outside Baskets"

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _fmt(val: Optional[float], suffix: str = "%", signed: bool = True) -> str:
    if val is None:
        return "N/A"
    sign = f"+{val:.2f}" if (signed and val >= 0) else f"{val:.2f}"
    return f"{sign}{suffix}"


def _parse_value(raw: str) -> Optional[float]:
    try:
        return float(raw.replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


# ── PARSE CSV ─────────────────────────────────────────────────────────────────

def parse_positions(path: Path) -> dict[str, list[dict]]:
    """Return {basket_name: [{symbol, value}]} from a Fidelity positions CSV."""
    baskets: dict[str, list[dict]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if not symbol or symbol in SKIP_SYMBOLS:
                continue
            value = _parse_value(row.get("Current Value") or "")
            if value is None or value <= 0:
                continue
            basket = (row.get("Basket Name") or "").strip() or OUTSIDE_LABEL
            baskets[basket].append({"symbol": symbol, "value": value})
    return dict(baskets)


# ── FETCH RETURNS ─────────────────────────────────────────────────────────────

def fetch_1m_returns(tickers: list[str], end: date, days: int) -> dict[str, Optional[float]]:
    """
    Download adjusted-close history and return price-only % change per ticker.
    Uses a buffer of extra calendar days so we always get at least one start
    and one end trading-day close.
    """
    start = end - timedelta(days=days + 10)
    cutoff = end - timedelta(days=days)      # first eligible start close

    click.echo(
        f"Fetching price history for {len(tickers)} tickers "
        f"({start} → {end}, measuring from ~{cutoff})..."
    )

    data = yf.download(
        tickers,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),   # inclusive end
        auto_adjust=True,
        progress=False,
        group_by="ticker" if len(tickers) > 1 else "column",
    )

    returns: dict[str, Optional[float]] = {}
    price_series: dict[str, pd.Series] = {}

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                closes = data["Close"].dropna()
            else:
                closes = data[ticker]["Close"].dropna()

            # Full window series for equity curves
            full_closes = closes[
                (closes.index >= pd.Timestamp(cutoff))
                & (closes.index <= pd.Timestamp(end))
            ]

            if len(full_closes) >= 2:
                price_series[ticker] = full_closes
                start_price = float(full_closes.iloc[0])
                end_price = float(full_closes.iloc[-1])
                returns[ticker] = ((end_price / start_price) - 1.0) * 100.0
            else:
                returns[ticker] = None
        except (KeyError, IndexError, TypeError):
            returns[ticker] = None

    price_df = pd.DataFrame(price_series) if price_series else pd.DataFrame()
    return returns, price_df


# ── EQUITY CURVES ────────────────────────────────────────────────────────────

def compute_basket_equity_curves(
    baskets: dict[str, list[dict]],
    price_df: pd.DataFrame,
) -> pd.DataFrame:
    """Return DataFrame of daily cumulative-return (%) per basket. Starts at 0."""
    if price_df.empty:
        return pd.DataFrame()

    curves = {}
    for basket, positions in baskets.items():
        total_value = sum(p["value"] for p in positions)
        if total_value <= 0:
            continue

        basket_series: Optional[pd.Series] = None
        covered_value = 0.0

        for p in positions:
            sym = p["symbol"]
            if sym not in price_df.columns:
                continue
            prices = price_df[sym].dropna()
            if len(prices) < 2:
                continue
            norm = prices / float(prices.iloc[0])   # 1.0 at start
            w = p["value"] / total_value
            weighted = norm * w
            basket_series = weighted if basket_series is None else basket_series.add(weighted, fill_value=0)
            covered_value += p["value"]

        if basket_series is not None and covered_value > 0:
            coverage = covered_value / total_value
            basket_series = basket_series / coverage        # rescale to full coverage
            curves[basket] = (basket_series - 1.0) * 100.0 # convert to %

    return pd.DataFrame(curves) if curves else pd.DataFrame()


# ── AGGREGATE ─────────────────────────────────────────────────────────────────

def aggregate(
    positions: list[dict], returns: dict[str, Optional[float]]
) -> tuple[Optional[float], list[dict]]:
    """Compute dollar-weighted basket return and enriched position details."""
    total_value = sum(p["value"] for p in positions)
    if total_value <= 0:
        return None, []

    details = []
    weighted_sum = 0.0
    covered_value = 0.0

    for p in sorted(positions, key=lambda x: -x["value"]):
        ret = returns.get(p["symbol"])
        w = p["value"] / total_value
        contrib = w * ret if ret is not None else None
        if ret is not None:
            weighted_sum += w * ret
            covered_value += p["value"]
        details.append(
            {
                "symbol": p["symbol"],
                "value": p["value"],
                "weight_pct": w * 100.0,
                "return_1m_pct": ret,
                "contribution_pct": contrib,
            }
        )

    coverage = covered_value / total_value
    basket_ret = (weighted_sum / coverage) if coverage > 0 else None
    return basket_ret, details


# ── REPORT ────────────────────────────────────────────────────────────────────

def build_report(
    baskets: dict[str, list[dict]],
    basket_returns: dict[str, Optional[float]],
    basket_details: dict[str, list[dict]],
    end: date,
    days: int,
) -> str:
    start_approx = end - timedelta(days=days)
    lines: list[str] = []

    lines += [
        "# Basket 1-Month Performance Backtest",
        "",
        "Educational note — not financial advice. Returns are **price-only** "
        "(no dividends) using Yahoo Finance adjusted daily closes.",
        "",
        f"- **Period:** ~{start_approx} → {end} ({days} calendar days)",
        f"- **Method:** dollar-weighted position return per basket",
        f"- **Composition:** May 8 holdings used as holding-period proxy",
        "",
    ]

    # ── Summary table ──────────────────────────────────────────────────────
    lines += [
        "## Basket Summary",
        "",
        "| Rank | Basket | Value | 1-Month Return |",
        "|---:|---|---:|---:|",
    ]

    ordered = sorted(
        baskets.keys(),
        key=lambda b: basket_returns.get(b) if basket_returns.get(b) is not None else -999.0,
        reverse=True,
    )

    for rank, basket in enumerate(ordered, 1):
        val = sum(p["value"] for p in baskets[basket])
        ret = basket_returns.get(basket)
        lines.append(f"| {rank} | {basket} | ${val:,.2f} | {_fmt(ret)} |")
    lines.append("")

    # ── Portfolio-level estimate ───────────────────────────────────────────
    total_portfolio = sum(
        sum(p["value"] for p in pos) for pos in baskets.values()
    )
    pw_sum = 0.0
    pw_cov = 0.0
    for basket, positions in baskets.items():
        ret = basket_returns.get(basket)
        if ret is not None:
            bval = sum(p["value"] for p in positions)
            pw_sum += (bval / total_portfolio) * ret
            pw_cov += bval

    if pw_cov > 0:
        port_ret = pw_sum * (total_portfolio / pw_cov)
        lines += [
            f"**Portfolio weighted 1-month return (equity + ETFs, ex-cash/pending):"
            f" {_fmt(port_ret)}**",
            "",
        ]

    # ── Per-basket detail ──────────────────────────────────────────────────
    lines += ["## Position Detail by Basket", ""]

    for basket in ordered:
        bval = sum(p["value"] for p in baskets[basket])
        ret = basket_returns.get(basket)
        lines += [
            f"### {basket}",
            "",
            f"Value: **${bval:,.2f}** | 1-Month Return: **{_fmt(ret)}**",
            "",
            "| Symbol | Value | Weight | 1-Month Return | Contribution |",
            "|---|---:|---:|---:|---:|",
        ]
        for d in basket_details.get(basket, []):
            lines.append(
                f"| {d['symbol']} "
                f"| ${d['value']:,.2f} "
                f"| {d['weight_pct']:.1f}% "
                f"| {_fmt(d['return_1m_pct'])} "
                f"| {_fmt(d['contribution_pct'])} |"
            )
        lines.append("")

    return "\n".join(lines)


# ── PLOTS ────────────────────────────────────────────────────────────────────

def plot_baskets(
    baskets: dict[str, list[dict]],
    basket_returns: dict[str, Optional[float]],
    basket_details: dict[str, list[dict]],
    price_df: pd.DataFrame,
    out_dir: Path,
    end: date,
    days: int,
) -> list[Path]:
    """Generate three performance charts and return the saved PNG paths."""
    saved: list[Path] = []
    date_str = end.strftime("%Y%m%d")
    start_approx = end - timedelta(days=days)

    valid = {b: r for b, r in basket_returns.items() if r is not None}
    ordered_asc = sorted(valid, key=lambda b: valid[b])         # low → high (for hbar)
    ordered_desc = list(reversed(ordered_asc))                  # high → low

    GREEN, RED = "#16a34a", "#dc2626"

    # ── Chart 1: Basket summary horizontal bar ─────────────────────────────
    fig1, ax1 = plt.subplots(figsize=(10, max(5, len(ordered_asc) * 0.65)))
    vals = [valid[b] for b in ordered_asc]
    colors = [GREEN if v >= 0 else RED for v in vals]
    bars = ax1.barh(ordered_asc, vals, color=colors, edgecolor="white", height=0.6)
    ax1.axvline(0, color="black", linewidth=0.8)
    for bar, val in zip(bars, vals):
        sign = "+" if val >= 0 else ""
        ax1.text(
            val + (0.8 if val >= 0 else -0.8),
            bar.get_y() + bar.get_height() / 2,
            f"{sign}{val:.1f}%",
            va="center", ha="left" if val >= 0 else "right",
            fontsize=9, fontweight="bold",
        )
    ax1.set_title(
        f"Basket 1-Month Returns  ({start_approx} → {end})",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax1.set_xlabel("Price-only return (%)", fontsize=10)
    ax1.tick_params(axis="y", labelsize=9)
    ax1.grid(axis="x", linestyle="--", alpha=0.4)
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    fig1.tight_layout()
    path1 = out_dir / f"basket_backtest_bars_{date_str}.png"
    fig1.savefig(path1, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    saved.append(path1)

    # ── Chart 2: Equity curves (cumulative return over time) ───────────────
    equity = compute_basket_equity_curves(baskets, price_df)
    if not equity.empty:
        palette = plt.colormaps["tab10"].resampled(len(equity.columns))
        fig2, ax2 = plt.subplots(figsize=(12, 7))
        final = equity.iloc[-1].sort_values(ascending=False)
        for i, basket in enumerate(final.index):
            if basket not in equity.columns:
                continue
            series = equity[basket].dropna()
            ax2.plot(series.index, series, linewidth=2.2, label=basket, color=palette(i))
        ax2.axhline(0, color="black", linewidth=0.8, linestyle="--")
        ax2.set_title(
            f"Basket Cumulative Return  ({start_approx} → {end})",
            fontsize=13, fontweight="bold", pad=12,
        )
        ax2.set_ylabel("Cumulative return (%)", fontsize=10)
        ax2.tick_params(axis="x", rotation=30, labelsize=8)
        ax2.grid(linestyle="--", alpha=0.35)
        ax2.legend(loc="upper left", fontsize=8, framealpha=0.75, ncol=2)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_visible(False)
        fig2.tight_layout()
        path2 = out_dir / f"basket_backtest_curves_{date_str}.png"
        fig2.savefig(path2, dpi=150, bbox_inches="tight")
        plt.close(fig2)
        saved.append(path2)

    # ── Chart 3: Per-basket position returns (one sub-panel per basket) ────
    n = len(ordered_desc)
    fig3, axes = plt.subplots(n, 1, figsize=(10, 2.4 * n), squeeze=False)
    for ax, basket in zip(axes[:, 0], ordered_desc):
        details = [d for d in basket_details.get(basket, []) if d["return_1m_pct"] is not None]
        if not details:
            ax.set_visible(False)
            continue
        syms = [d["symbol"] for d in details]
        rets = [d["return_1m_pct"] for d in details]
        clrs = [GREEN if r >= 0 else RED for r in rets]
        ax.barh(syms, rets, color=clrs, edgecolor="white", height=0.55)
        ax.axvline(0, color="black", linewidth=0.7)
        ax.set_title(
            f"{basket}  ({_fmt(valid.get(basket))})",
            fontsize=9, fontweight="bold", loc="left", pad=4,
        )
        ax.tick_params(axis="y", labelsize=8)
        ax.tick_params(axis="x", labelsize=7)
        ax.grid(axis="x", linestyle="--", alpha=0.35)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig3.suptitle(
        f"Position Returns by Basket  ({start_approx} → {end})",
        fontsize=12, fontweight="bold",
    )
    fig3.tight_layout()
    path3 = out_dir / f"basket_backtest_positions_{date_str}.png"
    fig3.savefig(path3, dpi=150, bbox_inches="tight")
    plt.close(fig3)
    saved.append(path3)

    return saved


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--csv", "csv_path", default=DEFAULT_CSV, show_default=True, help="Fidelity positions CSV file.")
@click.option("--days", default=31, show_default=True, help="Look-back window in calendar days.")
@click.option("--output-dir", default="output/reports", show_default=True, help="Directory for the markdown report.")
@click.option("--plot/--no-plot", default=True, show_default=True, help="Generate PNG charts.")
def main(csv_path: str, days: int, output_dir: str, plot: bool):
    """Run a 1-month basket performance backtest from a Fidelity positions CSV."""
    end_date = date(2026, 5, 8)
    csv_file = Path(csv_path)

    if not csv_file.exists():
        click.secho(f"CSV not found: {csv_file}", fg="red")
        sys.exit(1)

    click.echo(f"Parsing {csv_file}...")
    baskets = parse_positions(csv_file)

    tickers = [p["symbol"] for pos in baskets.values() for p in pos]
    click.echo(f"Found {len(tickers)} positions across {len(baskets)} baskets.")

    returns, price_df = fetch_1m_returns(tickers, end_date, days)

    found = sum(1 for v in returns.values() if v is not None)
    missing = [t for t, v in returns.items() if v is None]
    click.echo(f"Price data: {found}/{len(tickers)} tickers resolved.")
    if missing:
        click.secho(f"No data for: {', '.join(missing)}", fg="yellow")

    basket_returns: dict[str, Optional[float]] = {}
    basket_details: dict[str, list[dict]] = {}
    for basket, positions in baskets.items():
        ret, details = aggregate(positions, returns)
        basket_returns[basket] = ret
        basket_details[basket] = details

    report = build_report(basket_returns=basket_returns, basket_details=basket_details,
                          baskets=baskets, end=end_date, days=days)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"basket_backtest_1m_{end_date.strftime('%Y%m%d')}.md"
    out_path.write_text(report, encoding="utf-8")

    click.echo("\n" + report)
    click.secho(f"\nSaved → {out_path}", fg="green")

    if plot:
        click.echo("Generating charts...")
        chart_paths = plot_baskets(
            baskets=baskets,
            basket_returns=basket_returns,
            basket_details=basket_details,
            price_df=price_df,
            out_dir=out_dir,
            end=end_date,
            days=days,
        )
        for p in chart_paths:
            click.secho(f"Chart  → {p}", fg="cyan")
            os.startfile(str(p))


if __name__ == "__main__":
    main()
