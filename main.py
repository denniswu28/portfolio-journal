"""
main.py - CLI entry point for the portfolio tracker.

Commands:
    sync --input-file     Load portfolio from a Fidelity positions CSV file
  status                Show current portfolio summary
  analytics             Show performance metrics
  log-trade             Log a new trade with rationale
  history               Show recent trade history
  prompt                Generate an LLM prompt
    journal               Show a daily journal entry
    record-decision       Save an LLM response into the journal
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import click
from tabulate import tabulate

# ── PATH SETUP ────────────────────────────────────────────────────────────────
# Allow running `python main.py` from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from src.data_ingestion.csv_loader import CSVLoader
from src.data_ingestion.fidelity_analysis_loader import (
    bundle_has_data,
    load_analysis_bundle,
    load_analysis_bundles,
)
from src.data_ingestion.fidelity_bundle import (
    bundle_dir,
    move_existing_snapshots_for_date,
    organize_fidelity_exports,
)
from src.data_ingestion.market_data import get_price_history
from src.data_ingestion.models import PersistentContext
from src.portfolio.analytics import compute_metrics
from src.portfolio.optimizer import (
    METHODS,
    PERIODS_PER_YEAR,
    build_rebalance_plan,
    load_sleeve_universe,
    write_rebalance_csv,
    write_rebalance_markdown,
)
from src.portfolio.plots import (
    plot_asset_allocation,
    plot_geographic_exposure,
    plot_position_weights,
    plot_return_drawdown,
    plot_style_exposure,
    plot_unrealized_pnl,
)
from src.portfolio.reporting import (
    select_latest_snapshot_per_day,
    write_asset_allocation_summary,
    write_fidelity_periodic_returns,
    write_geographic_exposure_summary,
    write_metrics_summary,
    write_portfolio_timeseries,
    write_return_series,
    write_style_exposure_summary,
)
from src.portfolio.tracker import PortfolioTracker
from src.prompt_engine.formatter import estimate_tokens, truncate_to_budget
from src.prompt_engine.prompt_builder import generate_prompt
from src.trade_log.history import TradeHistory
from src.trade_log.journal import JournalStore
from src.trade_log.logger import TradeLogger
from src.utils.config_loader import load_persistent_context, load_settings


def _load_config():
    """Load settings and persistent context, with graceful fallback."""
    try:
        settings = load_settings()
    except FileNotFoundError:
        settings = {}
    try:
        ctx = load_persistent_context()
    except FileNotFoundError:
        ctx = PersistentContext()
    return settings, ctx


def _get_journal_store(settings):
    """Return the daily journal store configured for this workspace."""
    return JournalStore(settings.get("journal_file", "data/journal.json"))


def _compute_snapshot_metrics(tracker, trades, current_snapshot=None):
    """Compute metrics from saved snapshots, optionally including an unsaved snapshot."""
    snapshots = [tracker.load_snapshot(path) for path in tracker.list_snapshots()]
    if current_snapshot is not None and (
        not snapshots or snapshots[-1].timestamp != current_snapshot.timestamp
    ):
        snapshots.append(current_snapshot)
    snapshots = select_latest_snapshot_per_day(snapshots)
    if not snapshots:
        return None
    return compute_metrics(snapshots, trades)


def _get_latest_snapshot_path(tracker):
    """Return the latest saved snapshot path, if any."""
    snapshot_files = tracker.list_snapshots()
    if not snapshot_files:
        return None
    return snapshot_files[-1]


def _format_optional_number(value, precision=2, signed=False, suffix=""):
    """Format a possibly missing numeric value for CLI output."""
    if value is None:
        return "N/A"
    sign = "+" if signed else ""
    return f"{value:{sign}.{precision}f}{suffix}"


def _parse_bundle_date(value: str) -> date:
    """Parse a YYYY-MM-DD date for dated Fidelity export folders."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise click.BadParameter("Use YYYY-MM-DD format, for example 2026-05-06.")


def _load_analysis_bundle_if_available(folder: str | Path | None):
    """Load supplemental Fidelity analysis when a dated folder has supported CSVs."""
    if not folder:
        return None
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return None
    bundle = load_analysis_bundle(path)
    return bundle if bundle_has_data(bundle) else None


def _load_analysis_for_snapshot(settings, snapshot_path, snapshot):
    """Find the dated analysis bundle that belongs to a snapshot."""
    snapshots_dir = Path(settings.get("snapshots_dir", "data/portfolio_snapshots"))
    candidate_dirs = []
    if snapshot_path:
        parent = Path(snapshot_path).parent
        if parent.name == snapshot.timestamp.date().isoformat():
            candidate_dirs.append(parent)
    candidate_dirs.append(snapshots_dir / snapshot.timestamp.date().isoformat())

    for folder in candidate_dirs:
        bundle = _load_analysis_bundle_if_available(folder)
        if bundle:
            return bundle
    return snapshot.fidelity_analysis


def _load_all_periodic_returns(snapshots_dir):
    """Load source-labeled Fidelity periodic returns from every dated bundle."""
    periodic_returns = []
    for bundle in load_analysis_bundles(snapshots_dir):
        periodic_returns.extend(bundle.periodic_returns)
    return periodic_returns


@click.group()
def cli():
    """Portfolio Tracker — sync holdings, analyse them, and generate LLM prompts."""


# ── ORGANIZE EXPORTS ─────────────────────────────────────────────────────────

@cli.command("organize-exports")
@click.option("--date", "bundle_date_text", required=True, help="Bundle date in YYYY-MM-DD format.")
@click.option("--move/--copy", "move_files", default=False, help="Move files instead of copying them.")
@click.option("--include-snapshots", is_flag=True, default=False, help="Move legacy root snapshots for the same date into the dated folder.")
@click.argument("files", nargs=-1, type=click.Path(exists=True))
def organize_exports(bundle_date_text, move_files, include_snapshots, files):
    """Group Fidelity CSV exports into a dated snapshot folder."""
    settings, _ = _load_config()
    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    bundle_date = _parse_bundle_date(bundle_date_text)

    organized = organize_fidelity_exports(
        files=files,
        snapshots_dir=snapshots_dir,
        bundle_date=bundle_date,
        move=move_files,
    )
    snapshot_moves = []
    if include_snapshots:
        snapshot_moves = move_existing_snapshots_for_date(snapshots_dir, bundle_date)

    target_dir = bundle_dir(snapshots_dir, bundle_date)
    if not organized and not snapshot_moves:
        click.secho("No supported files were organized.", fg="yellow")
        return

    click.secho(f"Bundle folder: {target_dir}", fg="green")
    for item in [*organized, *snapshot_moves]:
        click.echo(f"  {item.action}: {item.file_type} -> {item.target_path}")


# ── SYNC ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--input-file", "-i", required=True, help="Path to the Fidelity positions CSV file.")
@click.option("--cash", default=None, type=float, help="Cash balance override if you need to supplement the Fidelity CSV.")
@click.option("--refresh-prices", is_flag=True, default=False, help="Fetch live prices from yfinance.")
@click.option("--save/--no-save", default=True, help="Save snapshot to disk.")
def sync(input_file, cash, refresh_prices, save):
    """Sync portfolio from a Fidelity CSV and save a snapshot."""
    _sync_from_positions_file(input_file, cash, refresh_prices, save)


@cli.command("sync-bundle")
@click.option("--date", "bundle_date_text", required=True, help="Bundle date in YYYY-MM-DD format.")
@click.option("--cash", default=None, type=float, help="Cash balance override if you need to supplement the Fidelity CSV.")
@click.option("--refresh-prices", is_flag=True, default=False, help="Fetch live prices from yfinance.")
@click.option("--save/--no-save", default=True, help="Save snapshot to disk.")
def sync_bundle(bundle_date_text, cash, refresh_prices, save):
    """Sync a dated Fidelity bundle containing positions.csv and optional analysis CSVs."""
    settings, _ = _load_config()
    bundle_date = _parse_bundle_date(bundle_date_text)
    folder = bundle_dir(settings.get("snapshots_dir", "data/portfolio_snapshots"), bundle_date)
    positions_file = folder / "positions.csv"
    if not positions_file.exists():
        click.secho(f"No positions.csv found in {folder}.", fg="red")
        sys.exit(1)

    _sync_from_positions_file(positions_file, cash, refresh_prices, save, analysis_folder=folder)


def _sync_from_positions_file(input_file, cash, refresh_prices, save, analysis_folder=None):
    """Shared sync implementation for a single positions CSV or dated bundle."""
    settings, _ = _load_config()

    csv_loader = CSVLoader()

    try:
        raw_data = csv_loader.load(input_file, cash=cash or 0.0)
    except (FileNotFoundError, ValueError) as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)

    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir, refresh_prices=refresh_prices)

    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trades = trade_logger.load_all()
    journal_store = _get_journal_store(settings)

    snapshot = tracker.build_snapshot(raw_data, trade_history=trades)
    analysis_bundle = _load_analysis_bundle_if_available(analysis_folder or Path(input_file).parent)
    if analysis_bundle:
        snapshot.fidelity_analysis = analysis_bundle
    snapshot_path = ""

    if save:
        path = tracker.save_snapshot(snapshot)
        snapshot_path = str(path)
        click.secho(f"Snapshot saved: {path}", fg="green")

    metrics = _compute_snapshot_metrics(
        tracker,
        trades,
        current_snapshot=None if save else snapshot,
    )
    journal_store.record_snapshot(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        trades=trades,
        metrics=metrics,
        analysis_bundle=analysis_bundle,
    )

    # Show brief summary
    click.echo(f"\nPortfolio Value: ${snapshot.total_portfolio_value:,.2f}")
    click.echo(f"Cash:            ${snapshot.cash:,.2f}")
    click.echo(f"Cumulative Return: {snapshot.cumulative_return_pct:+.2f}%")
    click.echo(f"Positions: {len(snapshot.positions)}")
    if analysis_bundle:
        click.echo("Supplemental Fidelity analysis attached.")


# ── STATUS ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--snapshot", "snapshot_path", default=None, help="Path to a specific snapshot file.")
def status(snapshot_path):
    """Show current portfolio summary table."""
    settings, _ = _load_config()
    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir)

    if snapshot_path:
        try:
            snap = tracker.load_snapshot(snapshot_path)
        except FileNotFoundError:
            click.secho(f"Snapshot not found: {snapshot_path}", fg="red")
            sys.exit(1)
    else:
        snap = tracker.load_latest_snapshot()
        if not snap:
            click.secho("No snapshots found. Run 'python main.py sync' first.", fg="yellow")
            return

    click.echo(f"\n{'='*60}")
    click.echo(f"Portfolio Status — {snap.timestamp.strftime('%Y-%m-%d %H:%M')}")
    click.echo(f"{'='*60}")
    click.echo(f"Total Value:       ${snap.total_portfolio_value:>12,.2f}")
    click.echo(f"Cash:              ${snap.cash:>12,.2f}")
    click.echo(f"Invested:          ${snap.invested_value:>12,.2f}")
    click.echo(f"Today's Change:    ${snap.today_change:>+12,.2f}  ({snap.today_change_pct:+.2f}%)")
    click.echo(f"Total Gain/Loss:   ${snap.total_gain_loss:>+12,.2f}  ({snap.total_gain_loss_pct:+.2f}%)")
    click.echo(f"Cumulative Return: {snap.cumulative_return_pct:>+12.2f}%")

    if snap.positions:
        click.echo(f"\n{'─'*60}")
        click.echo("Holdings:")
        rows = []
        for p in sorted(snap.positions, key=lambda x: x.market_value, reverse=True):
            rows.append([
                p.ticker,
                p.company_name[:30],
                p.shares,
                f"${p.avg_cost_basis:.2f}",
                f"${p.current_price:.2f}",
                f"${p.market_value:,.2f}",
                f"{p.unrealized_pnl_pct:+.2f}%",
                f"{p.weight_pct:.1f}%",
            ])
        headers = ["Ticker", "Company", "Shares", "Avg Cost", "Price", "Value", "P&L %", "Weight"]
        click.echo(tabulate(rows, headers=headers, tablefmt="simple"))


# ── ANALYTICS ─────────────────────────────────────────────────────────────────

@cli.command()
def analytics():
    """Show portfolio performance metrics across all saved snapshots."""
    settings, _ = _load_config()
    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir)

    snapshots = select_latest_snapshot_per_day(
        [tracker.load_snapshot(path) for path in tracker.list_snapshots()]
    )
    if len(snapshots) < 2:
        click.secho(
            "Need at least 2 daily snapshots for metrics. Run 'sync' on another day or backfill an older CSV.",
            fg="yellow",
        )
        # Show current snapshot stats if available
        snap = tracker.load_latest_snapshot()
        if snap:
            click.echo(f"\nLatest snapshot: {snap.timestamp.strftime('%Y-%m-%d %H:%M')}")
            click.echo(f"Portfolio Value: ${snap.total_portfolio_value:,.2f}")
        return

    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trades = trade_logger.load_all()

    metrics = compute_metrics(snapshots, trades)

    click.echo(f"\n{'='*50}")
    click.echo("Performance Metrics")
    click.echo(f"{'='*50}")
    click.echo(f"Cumulative Return:   {metrics.cumulative_return_pct:>+10.2f}%")
    click.echo(f"Annualized Return:   {metrics.annualized_return_pct:>+10.2f}%")
    click.echo(f"Annualized Vol:      {metrics.annualized_volatility_pct:>10.2f}%")
    click.echo(f"Sharpe Ratio:        {_format_optional_number(metrics.sharpe_ratio, precision=4):>10}")
    click.echo(f"Calmar Ratio:        {_format_optional_number(metrics.calmar_ratio, precision=4):>10}")
    click.echo(f"Max Drawdown:        {metrics.max_drawdown_pct:>10.2f}%")
    click.echo(f"Win Rate:            {metrics.win_rate_pct:>10.1f}%")
    click.echo(f"Avg Win:             {metrics.avg_win_pct:>+10.2f}%")
    click.echo(f"Avg Loss:            {metrics.avg_loss_pct:>+10.2f}%")
    click.echo(f"Top-3 Concentration: {metrics.concentration_top3_pct:>10.1f}%")
    click.echo(f"Total Trades:        {metrics.total_trades:>10}")
    click.echo(f"  Winning:           {metrics.winning_trades:>10}")
    click.echo(f"  Losing:            {metrics.losing_trades:>10}")


# ── REPORT ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--output-dir", "-o", default=None, help="Directory to save report files.")
@click.option("--show/--no-show", default=False, help="Display plots interactively after saving.")
@click.option("--snapshot", "snapshot_path", default=None, help="Snapshot to use for latest-position charts.")
def report(output_dir, show, snapshot_path):
    """Generate plots and spreadsheet-compatible metrics reports."""
    settings, _ = _load_config()
    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir)

    snapshot_files = tracker.list_snapshots()
    if not snapshot_files:
        click.secho("No snapshots found. Run 'python main.py sync' first.", fg="yellow")
        return

    snapshots = select_latest_snapshot_per_day(
        [tracker.load_snapshot(path) for path in snapshot_files]
    )
    if len(snapshots) < 2:
        click.secho(
            "Only one daily snapshot found; return and drawdown history will be limited.",
            fg="yellow",
        )

    if snapshot_path:
        try:
            latest_snapshot = tracker.load_snapshot(snapshot_path)
        except FileNotFoundError:
            click.secho(f"Snapshot not found: {snapshot_path}", fg="red")
            sys.exit(1)
        latest_snapshot_path = snapshot_path
    else:
        latest_snapshot = snapshots[-1]
        latest_path = _get_latest_snapshot_path(tracker)
        latest_snapshot_path = str(latest_path) if latest_path else ""

    analysis_bundle = _load_analysis_for_snapshot(
        settings,
        latest_snapshot_path,
        latest_snapshot,
    )
    periodic_returns = _load_all_periodic_returns(snapshots_dir)

    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trades = trade_logger.load_all()
    metrics = compute_metrics(snapshots, trades)

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports"))
    report_dir.mkdir(parents=True, exist_ok=True)

    generated_files = [
        write_metrics_summary(metrics, report_dir / "metrics_summary.csv", latest_snapshot),
        write_portfolio_timeseries(snapshots, report_dir / "portfolio_timeseries.csv"),
        write_return_series(snapshots, periodic_returns, report_dir / "return_series.csv"),
        plot_return_drawdown(
            snapshots,
            report_dir / "return_drawdown.png",
            show=show,
            periodic_returns=periodic_returns,
        ),
    ]
    if periodic_returns:
        generated_files.append(
            write_fidelity_periodic_returns(
                periodic_returns,
                report_dir / "fidelity_periodic_returns.csv",
            )
        )
    if latest_snapshot.positions:
        generated_files.extend(
            [
                plot_unrealized_pnl(latest_snapshot, report_dir / "unrealized_pnl.png", show=show),
                plot_position_weights(latest_snapshot, report_dir / "position_weights.png", show=show),
            ]
        )
    if analysis_bundle:
        if analysis_bundle.asset_allocation:
            generated_files.extend(
                [
                    write_asset_allocation_summary(
                        analysis_bundle.asset_allocation,
                        report_dir / "asset_allocation_summary.csv",
                    ),
                    plot_asset_allocation(
                        analysis_bundle.asset_allocation,
                        report_dir / "asset_allocation.png",
                        show=show,
                    ),
                ]
            )
        if analysis_bundle.geographic_exposure:
            generated_files.extend(
                [
                    write_geographic_exposure_summary(
                        analysis_bundle.geographic_exposure,
                        report_dir / "geographic_exposure_summary.csv",
                    ),
                    plot_geographic_exposure(
                        analysis_bundle.geographic_exposure,
                        report_dir / "geographic_exposure.png",
                        show=show,
                    ),
                ]
            )
        if analysis_bundle.style_exposure:
            generated_files.extend(
                [
                    write_style_exposure_summary(
                        analysis_bundle.style_exposure,
                        report_dir / "style_summary.csv",
                    ),
                    plot_style_exposure(
                        analysis_bundle.style_exposure,
                        report_dir / "style_exposure.png",
                        show=show,
                    ),
                ]
            )

    click.echo(f"\n{'='*50}")
    click.echo("Report Metrics")
    click.echo(f"{'='*50}")
    click.echo(f"Cumulative Return:   {metrics.cumulative_return_pct:>+10.2f}%")
    click.echo(f"Annualized Return:   {metrics.annualized_return_pct:>+10.2f}%")
    click.echo(f"Annualized Vol:      {metrics.annualized_volatility_pct:>10.2f}%")
    click.echo(f"Sharpe Ratio:        {_format_optional_number(metrics.sharpe_ratio, precision=4):>10}")
    click.echo(f"Calmar Ratio:        {_format_optional_number(metrics.calmar_ratio, precision=4):>10}")
    click.echo(f"Max Drawdown:        {metrics.max_drawdown_pct:>10.2f}%")
    click.echo(f"Top-3 Concentration: {metrics.concentration_top3_pct:>10.1f}%")
    if analysis_bundle:
        click.echo("Supplemental Fidelity analysis included.")

    click.echo("\nGenerated files:")
    for path in generated_files:
        click.echo(f"  {path}")


# ── PORTFOLIO THEORY REBALANCE ───────────────────────────────────────────────

@cli.command("rebalance-weights")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml", help="YAML sleeve universe with proxies and constraints.")
@click.option("--method", default="erc", type=click.Choice(METHODS), help="Target weighting method.")
@click.option("--period", default="3y", help="Yahoo Finance history period, for example 3y or 5y.")
@click.option("--interval", default="1wk", type=click.Choice(["1d", "1wk", "1mo"]), help="Yahoo Finance history interval.")
@click.option("--cash-target-pct", default=None, type=float, help="Cash reserve target as percent of portfolio.")
@click.option("--risk-free-rate", default=None, type=float, help="Annual risk-free rate as a decimal, for example 0.04.")
@click.option("--min-observations", default=52, type=int, help="Minimum aligned return observations per proxy.")
@click.option("--min-trade-dollars", default=None, type=float, help="Small trade threshold for hold/buy/trim labels.")
@click.option("--output-dir", "output_dir", default=None, help="Directory to save rebalance report files.")
@click.option("--snapshot", "snapshot_path", default=None, help="Snapshot to map current dollars against.")
def rebalance_weights(
    universe_path,
    method,
    period,
    interval,
    cash_target_pct,
    risk_free_rate,
    min_observations,
    min_trade_dollars,
    output_dir,
    snapshot_path,
):
    """Optimize long-term sleeve targets from Yahoo Finance price history."""
    settings, _ = _load_config()
    try:
        universe_settings, sleeves = load_sleeve_universe(universe_path)
    except (FileNotFoundError, ValueError, KeyError) as error:
        click.secho(f"Error loading universe: {error}", fg="red")
        sys.exit(1)

    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir)
    latest_snapshot = None
    if snapshot_path:
        try:
            latest_snapshot = tracker.load_snapshot(snapshot_path)
        except FileNotFoundError:
            click.secho(f"Snapshot not found: {snapshot_path}", fg="red")
            sys.exit(1)
    else:
        latest_snapshot = tracker.load_latest_snapshot()

    proxies = [sleeve.proxy for sleeve in sleeves]
    click.echo(f"Fetching {period} {interval} price history for {len(proxies)} sleeve proxies...")
    price_history = get_price_history(proxies, period=period, interval=interval)
    if price_history.empty:
        click.secho("No Yahoo Finance price history returned for the sleeve universe.", fg="red")
        sys.exit(1)

    cash_target = cash_target_pct if cash_target_pct is not None else float(universe_settings.get("cash_target_pct", 10.0))
    selected_risk_free_rate = risk_free_rate if risk_free_rate is not None else float(universe_settings.get("risk_free_rate", 0.04))
    selected_min_trade = min_trade_dollars if min_trade_dollars is not None else float(universe_settings.get("min_trade_dollars", 25.0))
    try:
        plan = build_rebalance_plan(
            sleeves=sleeves,
            price_history=price_history,
            snapshot=latest_snapshot,
            method=method,
            cash_target_pct=cash_target,
            risk_free_rate=selected_risk_free_rate,
            periods_per_year=PERIODS_PER_YEAR[interval],
            min_observations=min_observations,
            min_trade_dollars=selected_min_trade,
        )
    except ValueError as error:
        click.secho(f"Error optimizing weights: {error}", fg="red")
        sys.exit(1)

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports"))
    generated_at = plan["generated_at"].strftime("%Y%m%d_%H%M%S")
    csv_path = write_rebalance_csv(plan, report_dir / f"rebalance_weights_{generated_at}.csv")
    markdown_path = write_rebalance_markdown(plan, report_dir / f"rebalance_plan_{generated_at}.md")

    click.echo(f"\n{'='*64}")
    click.echo("Portfolio Theory Rebalance")
    click.echo(f"{'='*64}")
    click.echo(f"Method: {method}")
    click.echo(f"History: {plan['data_start'].date()} to {plan['data_end'].date()} ({plan['observation_count']} observations)")
    if latest_snapshot:
        click.echo(f"Snapshot: {latest_snapshot.timestamp.strftime('%Y-%m-%d %H:%M')} | Value: ${latest_snapshot.total_portfolio_value:,.2f}")

    rows = []
    for row in [*plan["rows"], plan["cash_row"]]:
        rows.append(
            [
                row["sleeve"],
                row["proxy"],
                f"{row['target_weight_pct']:.2f}%" if row.get("target_weight_pct") is not None else "",
                f"{row['current_weight_pct']:.2f}%" if row.get("current_weight_pct") is not None else "",
                f"${row['trade_dollars']:,.2f}" if isinstance(row.get("trade_dollars"), (int, float)) else "",
                row["action"],
            ]
        )
    click.echo(tabulate(rows, headers=["Sleeve", "Proxy", "Target", "Current", "Trade", "Action"], tablefmt="simple"))

    if plan["missing_proxies"]:
        click.secho("Missing/insufficient history: " + ", ".join(plan["missing_proxies"]), fg="yellow")
    if plan["unmapped_positions"]:
        unmapped = ", ".join(position["ticker"] for position in plan["unmapped_positions"])
        click.secho("Unmapped current holdings: " + unmapped, fg="yellow")

    click.echo("\nGenerated files:")
    click.echo(f"  {csv_path}")
    click.echo(f"  {markdown_path}")


# ── LOG TRADE ─────────────────────────────────────────────────────────────────

@cli.command("log-trade")
@click.option("--ticker", "-t", required=True, help="Ticker symbol (e.g. AAPL).")
@click.option("--action", "-a", required=True, type=click.Choice(["BUY", "SELL"], case_sensitive=False), help="BUY or SELL.")
@click.option("--shares", "-s", required=True, type=float, help="Number of shares.")
@click.option("--price", "-p", required=True, type=float, help="Execution price per share.")
@click.option("--rationale", "-r", default="", help="Reason for the trade.")
@click.option("--tags", default="", help="Comma-separated tags (e.g. 'momentum,earnings').")
def log_trade(ticker, action, shares, price, rationale, tags):
    """Log a new trade with rationale."""
    settings, _ = _load_config()
    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    journal_store = _get_journal_store(settings)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if not rationale:
        rationale = click.prompt("Trade rationale (or press Enter to skip)", default="")

    trade = trade_logger.log_trade(
        ticker=ticker,
        action=action.upper(),
        shares=shares,
        price=price,
        rationale=rationale,
        tags=tag_list,
    )
    journal_store.add_trade(trade)

    click.secho(
        f"Trade logged: {trade.action} {trade.shares} {trade.ticker} @ ${trade.price:.2f}",
        fg="green",
    )
    click.echo(f"ID: {trade.id}")
    if trade.rationale:
        click.echo(f"Rationale: {trade.rationale}")


# ── HISTORY ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--n", default=10, help="Number of recent trades to show.")
@click.option("--ticker", default=None, help="Filter by ticker.")
@click.option("--action", default=None, type=click.Choice(["BUY", "SELL"], case_sensitive=False), help="Filter by action.")
def history(n, ticker, action):
    """Show recent trade history."""
    settings, _ = _load_config()
    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trade_history = TradeHistory(trade_logger)

    if ticker:
        trades = trade_history.get_by_ticker(ticker)
    elif action:
        trades = trade_history.get_by_action(action)
    else:
        trades = trade_history.get_recent(n)

    if not trades:
        click.echo("No trades found.")
        return

    summary = trade_history.summary()
    click.echo(f"\nTrade History ({summary['total_trades']} total | "
               f"{summary['buy_trades']} buys | {summary['sell_trades']} sells)")
    click.echo("─" * 70)

    rows = []
    for t in trades[-n:]:
        rows.append([
            t.timestamp.strftime("%Y-%m-%d"),
            t.action,
            t.ticker,
            t.shares,
            f"${t.price:.2f}",
            f"${t.total_value:,.2f}",
            (t.rationale[:40] + "...") if len(t.rationale) > 40 else t.rationale,
        ])
    headers = ["Date", "Action", "Ticker", "Shares", "Price", "Total", "Rationale"]
    click.echo(tabulate(rows, headers=headers, tablefmt="simple"))


# ── JOURNAL ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--date", "entry_date", default=None, help="Journal date in YYYY-MM-DD format. Defaults to the latest entry.")
def journal(entry_date):
    """Show the recorded daily journal entry."""
    settings, _ = _load_config()
    journal_store = _get_journal_store(settings)
    entries = journal_store.load_all()

    if not entries:
        click.secho("No journal entries found yet.", fg="yellow")
        return

    entry = journal_store.get_entry(entry_date) if entry_date else entries[-1]
    if not entry:
        click.secho(f"No journal entry found for {entry_date}.", fg="yellow")
        return

    click.echo(f"\n{'='*60}")
    click.echo(f"Journal Entry — {entry.entry_date}")
    click.echo(f"Updated: {entry.updated_at.strftime('%Y-%m-%d %H:%M')}")
    click.echo(f"{'='*60}")

    if entry.snapshot:
        snap = entry.snapshot
        click.echo("Snapshot Summary")
        click.echo(f"  Total Value: ${snap.total_value:,.2f}")
        click.echo(f"  Cash:        ${snap.cash:,.2f}")
        click.echo(f"  Positions:    {snap.positions_count}")
        if snap.snapshot_path:
            click.echo(f"  Snapshot:     {snap.snapshot_path}")

    if entry.pnl_summary:
        pnl = entry.pnl_summary
        click.echo("\nP&L Summary")
        click.echo(f"  Realized P&L:   ${pnl.realized_pnl:,.2f}")
        click.echo(f"  Unrealized P&L: ${pnl.unrealized_pnl:,.2f} ({pnl.unrealized_pnl_pct:+.2f}%)")
        click.echo(f"  Today's Change: ${pnl.today_change:,.2f} ({pnl.today_change_pct:+.2f}%)")
        click.echo(f"  Cumulative:     {pnl.cumulative_return_pct:+.2f}%")

    if entry.exposure_summary:
        exposure = entry.exposure_summary
        click.echo("\nFidelity Exposure")
        if exposure.top_asset_classes:
            click.echo(f"  Asset Classes: {', '.join(exposure.top_asset_classes)}")
        if exposure.top_regions:
            click.echo(f"  Regions:       {', '.join(exposure.top_regions)}")
        if exposure.top_countries:
            click.echo(f"  Countries:     {', '.join(exposure.top_countries)}")
        if exposure.top_styles:
            click.echo(f"  Styles:        {', '.join(exposure.top_styles)}")
        if exposure.fidelity_period_end:
            ytd = _format_optional_number(exposure.fidelity_twr_ytd_pct, signed=True, suffix="%")
            life = _format_optional_number(exposure.fidelity_twr_life_pct, signed=True, suffix="%")
            click.echo(f"  Fidelity TWR:  YTD {ytd}, life {life} as of {exposure.fidelity_period_end}")

    click.echo(f"\nTrades ({len(entry.trades)})")
    if entry.trades:
        for trade in entry.trades:
            click.echo(
                f"  - {trade.timestamp.strftime('%H:%M')} {trade.action} {trade.shares} "
                f"{trade.ticker} @ ${trade.price:.2f}"
            )
    else:
        click.echo("  No trades recorded.")

    click.echo(f"\nPrompts ({len(entry.prompts)})")
    if entry.prompts:
        for prompt_record in entry.prompts:
            click.echo(
                f"  - {prompt_record.created_at.strftime('%H:%M')} [{prompt_record.prompt_type}] "
                f"{prompt_record.question}"
            )
    else:
        click.echo("  No prompts recorded.")

    click.echo(f"\nDecisions ({len(entry.decisions)})")
    if entry.decisions:
        for decision in entry.decisions:
            label = decision.summary or "Decision recorded"
            click.echo(f"  - {decision.recorded_at.strftime('%H:%M')} {label}")
    else:
        click.echo("  No LLM decisions recorded.")


# ── PROMPT ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--type", "prompt_type", default="trade",
              type=click.Choice(["trade", "review", "risk"]),
              help="Prompt type: trade (default), review, or risk.")
@click.option("--question", "-q", default=None, help="Specific question for the LLM.")
@click.option("--recent-trades", "recent_n", default=10, help="Number of recent trades to include.")
@click.option("--max-tokens", default=4000, help="Maximum prompt token budget.")
@click.option("--output", "-o", default=None, help="Save prompt to a file.")
@click.option("--snapshot", "snapshot_path", default=None, help="Path to a specific snapshot file.")
def prompt(prompt_type, question, recent_n, max_tokens, output, snapshot_path):
    """Generate an LLM prompt from the current portfolio state."""
    settings, persistent_ctx = _load_config()
    journal_store = _get_journal_store(settings)

    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir)
    resolved_snapshot_path = snapshot_path

    if snapshot_path:
        try:
            snap = tracker.load_snapshot(snapshot_path)
        except FileNotFoundError:
            click.secho(f"Snapshot not found: {snapshot_path}", fg="red")
            sys.exit(1)
    else:
        snap = tracker.load_latest_snapshot()
        if not snap:
            click.secho("No snapshots found. Run 'python main.py sync' first.", fg="yellow")
            sys.exit(1)
        latest_snapshot_path = _get_latest_snapshot_path(tracker)
        resolved_snapshot_path = str(latest_snapshot_path) if latest_snapshot_path else ""

    analysis_bundle = _load_analysis_for_snapshot(settings, resolved_snapshot_path, snap)

    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trade_history_obj = TradeHistory(trade_logger)
    recent_trades = trade_history_obj.get_recent(recent_n)

    # Try to compute metrics from daily snapshots
    all_snapshots = select_latest_snapshot_per_day(
        [tracker.load_snapshot(path) for path in tracker.list_snapshots()]
    )
    metrics = None
    if len(all_snapshots) >= 2:
        metrics = compute_metrics(all_snapshots, trade_logger.load_all())

    user_question = question or click.prompt(
        "What would you like to ask the LLM?",
        default="Analyze my portfolio and recommend trades for this week.",
    )
    prompt_created_at = datetime.now()

    try:
        rendered = generate_prompt(
            prompt_type=prompt_type,
            snapshot=snap,
            recent_trades=recent_trades,
            metrics=metrics,
            persistent_ctx=persistent_ctx,
            user_question=user_question,
            analysis_bundle=analysis_bundle,
        )
    except KeyError as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)

    rendered = truncate_to_budget(rendered, max_tokens=max_tokens)
    token_count = estimate_tokens(rendered)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        click.secho(f"Prompt saved to: {output_path}  (~{token_count} tokens)", fg="green")
    else:
        # Also auto-save to output/prompts/
        output_dir = Path(settings.get("output_dir", "output/prompts"))
        output_dir.mkdir(parents=True, exist_ok=True)
        auto_file = output_dir / f"prompt_{prompt_type}_{prompt_created_at.strftime('%Y%m%d_%H%M%S')}.txt"
        auto_file.write_text(rendered, encoding="utf-8")
        output_path = auto_file

        click.echo("\n" + "=" * 60)
        click.echo(rendered)
        click.echo("=" * 60)
        click.echo(f"\nPrompt length: ~{token_count} tokens")
        click.secho(f"Auto-saved to: {auto_file}", fg="green")

    journal_store.add_prompt(
        prompt_type=prompt_type,
        question=user_question,
        output_path=output_path,
        snapshot_path=resolved_snapshot_path or "",
        token_count=token_count,
        created_at=prompt_created_at,
    )


# ── DECISION LOG ──────────────────────────────────────────────────────────────

@cli.command("record-decision")
@click.option("--date", "entry_date", default=None, help="Journal date in YYYY-MM-DD format. Defaults to today.")
@click.option("--prompt-file", default="", help="Prompt file associated with the LLM response.")
@click.option("--summary", default="", help="Short summary of the decision or recommendation.")
@click.option("--response-file", default=None, help="Read the LLM response from a text file.")
@click.option("--response-text", default=None, help="Inline LLM response text.")
def record_decision(entry_date, prompt_file, summary, response_file, response_text):
    """Record the LLM response for the daily journal."""
    settings, _ = _load_config()
    journal_store = _get_journal_store(settings)

    if response_file:
        response = Path(response_file).read_text(encoding="utf-8").strip()
    elif response_text:
        response = response_text.strip()
    else:
        click.echo("Paste the LLM response below. When done, press Ctrl+D (Unix) or Ctrl+Z then Enter (Windows):")
        response = sys.stdin.read().strip()

    if not response:
        click.secho("No LLM response provided.", fg="red")
        sys.exit(1)

    journal_key = entry_date or datetime.now().date().isoformat()
    entry = journal_store.add_decision(
        entry_date=journal_key,
        response_text=response,
        summary=summary,
        prompt_output_path=prompt_file,
    )

    click.secho(f"Decision logged for {entry.entry_date}.", fg="green")


if __name__ == "__main__":
    cli()
