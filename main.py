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
from datetime import date, datetime, timedelta
from pathlib import Path

import click
import pandas as pd
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
from src.data_ingestion.market_data import (
    get_current_price,
    get_current_prices,
    get_option_chain,
    get_price_history,
    get_risk_free_rate,
    list_option_expiries,
    nearest_expiry,
    realized_volatility,
)
from src.data_ingestion.models import PersistentContext
from src.portfolio.analytics import compute_metrics
from src.portfolio.baskets import (
    build_baskets,
    compute_basket_metrics,
    recompose_basket,
    resize_basket,
    write_basket_order_plan,
)
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
    plot_basket_weights,
    plot_geographic_exposure,
    plot_option_payoff,
    plot_position_weights,
    plot_return_drawdown,
    plot_style_exposure,
    plot_unrealized_pnl,
)
from src.portfolio.reporting import (
    select_latest_snapshot_per_day,
    write_asset_allocation_summary,
    write_basket_summary,
    write_fidelity_periodic_returns,
    write_geographic_exposure_summary,
    write_metrics_summary,
    write_portfolio_timeseries,
    write_return_series,
    write_style_exposure_summary,
)
from src.portfolio.tracker import PortfolioTracker
from src.options.events import load_event_calendar
from src.options.models import (
    SECURED_CASH,
    SECURED_SHORT_STOCK,
    SECURED_STOCK,
    OptionLeg,
    OptionStrategy,
)
from src.options.monitor import all_alerts, monitor_positions, write_monitor_report
from src.options.reporting import order_ticket_lines, write_option_ticket
from src.options.risk import aggregate_greeks, options_sleeve_status, stress_test
from src.options.screener import screen_chain
from src.options.strategies import analyze_strategy, build_named_strategy, validate_level2
from src.prompt_engine.formatter import estimate_tokens, truncate_to_budget
from src.prompt_engine.prompt_builder import generate_prompt
from src.trade_log.history import TradeHistory
from src.trade_log.journal import JournalStore
from src.trade_log.logger import TradeLogger
from src.trade_log.option_logger import OptionTradeLogger
from src.utils.config_loader import load_persistent_context, load_settings
from src.advisory.gating import options_gate_status
from src.advisory.models import OptionAdvisorySummary
from src.advisory.orchestrator import build_advisory_run, known_tickers
from src.advisory.reporting import write_advisory
from src.advisory.thesis import build_thesis_context
from src.quant import reporting as quant_reporting
from src.quant.backtest import BacktestEngine, walk_forward
from src.quant.factor import DEFAULT_FACTOR_PROXIES, build_factor_model, variance_decomposition
from src.quant.optimize import ParamSpace, walk_forward_optimize
from src.quant.options_backtest import OptionBacktestConfig, backtest_option_structure
from src.quant.signals import compute_universe_signals
from src.quant.strategies_quant import (
    EqualWeightStrategy,
    MomentumStrategy,
    SleeveRebalanceStrategy,
)


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
        try:
            _, report_sleeves = load_sleeve_universe("config/growth_universe.yaml")
        except (FileNotFoundError, ValueError, KeyError):
            report_sleeves = []
        basket_view = build_baskets(latest_snapshot, report_sleeves)
        if basket_view.baskets:
            basket_metrics = compute_basket_metrics(basket_view)
            generated_files.extend(
                [
                    write_basket_summary(basket_metrics, report_dir / "basket_summary.csv"),
                    plot_basket_weights(basket_metrics, report_dir / "basket_weights.png", show=show),
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


# ── BASKET PLAN ───────────────────────────────────────────────────────────────

def _parse_weight_spec(spec: str) -> dict[str, float]:
    """Parse a 'TICKER=PCT,TICKER=PCT' recompose spec into a weight dict."""
    weights: dict[str, float] = {}
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"expected TICKER=PCT, got '{chunk}'")
        ticker, pct = chunk.split("=", 1)
        ticker = ticker.strip().upper()
        try:
            weights[ticker] = float(pct.strip())
        except ValueError:
            raise ValueError(f"invalid percent for {ticker}: '{pct.strip()}'")
    if not weights:
        raise ValueError("no weights parsed")
    return weights


def _print_basket_view(view) -> None:
    """Print the basket decomposition table."""
    click.echo(f"\n{'='*64}")
    click.echo("Basket Decomposition")
    click.echo(f"{'='*64}")
    click.echo(f"Portfolio value: ${view.portfolio_value:,.2f} | Cash: ${view.cash:,.2f}")
    rows = []
    for b in view.baskets:
        band = f"{b.band_min_pct:.0f}-{b.band_max_pct:.0f}%" if b.band_min_pct is not None else "N/A"
        rows.append(
            [b.name, f"${b.total_value:,.2f}", f"{b.weight_in_portfolio_pct:.2f}%", band, b.band_status(), len(b.components)]
        )
    click.echo(tabulate(rows, headers=["Basket", "Value", "Weight", "Band", "Status", "Holdings"], tablefmt="simple"))
    if view.out_of_basket:
        oob = ", ".join(p.ticker for p in sorted(view.out_of_basket, key=lambda p: p.market_value, reverse=True))
        click.echo(f"\nOut-of-basket (handled individually): {oob}")
    if view.mismatches:
        click.secho(f"\n{len(view.mismatches)} basket/sleeve mismatch(es):", fg="yellow")
        for m in view.mismatches:
            click.echo(f"  - {m}")


def _print_basket_plan(plan) -> None:
    """Print a basket rebalance plan's order table."""
    click.echo(f"\n{'='*64}")
    label = "Method A - recompose" if plan.method == "recompose" else "Method B - resize"
    click.echo(f"{plan.basket} | {label}")
    click.echo(f"{'='*64}")
    click.echo(f"Basket total: ${plan.current_total:,.2f} -> ${plan.target_total:,.2f}")
    if plan.band_min_pct is not None:
        click.echo(
            f"Band {plan.band_min_pct:.0f}-{plan.band_max_pct:.0f}% | "
            f"before {plan.band_status_before} -> after {plan.band_status_after}"
        )
    rows = []
    for o in plan.orders:
        rows.append([o.action, o.ticker, o.order_type, f"${o.dollars:,.2f}", f"{o.shares:.3f}", f"{o.current_pct:.1f}%->{o.target_pct:.1f}%"])
    click.echo(tabulate(rows, headers=["Action", "Ticker", "Type", "Trade $", "Shares", "Basket %"], tablefmt="simple"))
    click.echo(f"Net cash impact: ${plan.net_cash:,.2f}")
    for note in plan.notes:
        click.secho(f"Note: {note}", fg="yellow")


@cli.command("basket-plan")
@click.option("--snapshot", "snapshot_path", default=None, help="Snapshot to plan against (default: latest).")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml", help="Sleeve universe for policy bands.")
@click.option("--basket", "basket_name", default=None, help="Basket to change. Omit to show the basket decomposition.")
@click.option("--recompose", "recompose_spec", default=None, help='Method A: change component %, e.g. "MSFT=10,IGV=10,GOOG=25".')
@click.option("--new-total", "new_total", default=None, type=float, help="Method A: override the basket total $ (default keeps it fixed).")
@click.option("--resize-to", "resize_to", default=None, type=float, help="Method B: scale the whole basket to this total $.")
@click.option("--resize-by", "resize_by", default=None, type=float, help="Method B: add (+) or remove (-) this many $ from the whole basket.")
@click.option("--min-trade-dollars", "min_trade_dollars", default=None, type=float, help="Suppress trades below this size.")
@click.option("--output-dir", "output_dir", default=None, help="Directory for the order-plan markdown.")
def basket_plan(snapshot_path, universe_path, basket_name, recompose_spec, new_total, resize_to, resize_by, min_trade_dollars, output_dir):
    """Plan a portfolio change via Method A (recompose) or Method B (resize)."""
    settings, _ = _load_config()
    try:
        universe_settings, sleeves = load_sleeve_universe(universe_path)
    except (FileNotFoundError, ValueError, KeyError) as error:
        click.secho(f"Error loading universe: {error}", fg="red")
        sys.exit(1)

    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir)
    if snapshot_path:
        try:
            snapshot = tracker.load_snapshot(snapshot_path)
        except FileNotFoundError:
            click.secho(f"Snapshot not found: {snapshot_path}", fg="red")
            sys.exit(1)
    else:
        snapshot = tracker.load_latest_snapshot()
    if snapshot is None:
        click.secho("No snapshot available. Run `sync` first.", fg="red")
        sys.exit(1)

    view = build_baskets(snapshot, sleeves)

    if not basket_name:
        _print_basket_view(view)
        return

    basket = view.get(basket_name)
    if basket is None:
        names = ", ".join(b.name for b in view.baskets)
        click.secho(f"Basket '{basket_name}' not found. Available: {names}", fg="red")
        sys.exit(1)

    min_trade = min_trade_dollars if min_trade_dollars is not None else float(universe_settings.get("min_trade_dollars", 25.0))

    is_recompose = recompose_spec is not None
    is_resize = resize_to is not None or resize_by is not None
    if not is_recompose and not is_resize:
        click.secho("Specify a method: --recompose (A) or --resize-to/--resize-by (B).", fg="red")
        sys.exit(1)
    if is_recompose and is_resize:
        click.secho("Choose only one method per run: recompose (A) OR resize (B).", fg="red")
        sys.exit(1)
    if resize_to is not None and resize_by is not None:
        click.secho("Use only one of --resize-to or --resize-by.", fg="red")
        sys.exit(1)

    if is_recompose:
        try:
            target_weights = _parse_weight_spec(recompose_spec)
        except ValueError as error:
            click.secho(f"Invalid --recompose spec: {error}", fg="red")
            sys.exit(1)
        new_tickers = [t for t in target_weights if basket.component(t) is None]
        fetched = get_current_prices(new_tickers) if new_tickers else {}
        prices = {k: v for k, v in fetched.items() if v}
        missing = [t for t in new_tickers if t not in prices]
        if missing:
            click.secho(f"Could not fetch prices for: {', '.join(missing)}. They will be skipped.", fg="yellow")
        plan = recompose_basket(
            basket, target_weights, view.portfolio_value,
            prices=prices, new_total=new_total, min_trade_dollars=min_trade,
        )
    else:
        plan = resize_basket(
            basket, view.portfolio_value,
            new_total=resize_to, delta_dollars=resize_by, min_trade_dollars=min_trade,
        )

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports")) / snapshot.timestamp.date().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_basket = basket.name.lower().replace(" ", "_").replace("/", "-")
    out_path = report_dir / f"basket_plan_{safe_basket}_{stamp}.md"
    context_notes = [
        f"Snapshot: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}",
        f"Method: {'A (recompose)' if plan.method == 'recompose' else 'B (resize)'}",
    ]
    write_basket_order_plan([plan], view, out_path, title=f"Basket Plan - {basket.name}", context_notes=context_notes)

    _print_basket_plan(plan)
    click.echo(f"\nOrder plan written to: {out_path}")


# ── OPTIONS ───────────────────────────────────────────────────────────────────

def _resolve_expiry(ticker: str, expiry: str | None, target_dte: int = 45) -> date | None:
    """Resolve an option expiry: explicit date, or the listed expiry nearest target DTE."""
    if expiry:
        return date.fromisoformat(expiry)
    expiries = list_option_expiries(ticker)
    if not expiries:
        return None
    picked = nearest_expiry(expiries, date.today() + timedelta(days=target_dte))
    return date.fromisoformat(picked) if picked else None


def _parse_leg_spec(spec: str, underlying: str, expiry: date) -> OptionLeg:
    """Parse 'ACTION RIGHT STRIKE [CONTRACTS]' into an OptionLeg."""
    parts = spec.split()
    if len(parts) < 3:
        raise ValueError(f"leg '{spec}' must be 'ACTION RIGHT STRIKE [CONTRACTS]'")
    contracts = int(parts[3]) if len(parts) > 3 else 1
    return OptionLeg(
        underlying=underlying, right=parts[1], strike=float(parts[2]),
        expiry=expiry, action=parts[0], contracts=contracts,
    )


def _chain_iv(chain, right: str, strike: float) -> float | None:
    """Read the implied volatility at the nearest strike from a chain side."""
    if chain is None:
        return None
    df = chain.side(right)
    if df is None or df.empty or "impliedVolatility" not in df.columns or "strike" not in df.columns:
        return None
    idx = (df["strike"] - strike).abs().idxmin()
    iv = float(df.loc[idx, "impliedVolatility"])
    return iv if iv > 0 else None


@cli.command("options-chain")
@click.option("--ticker", "-t", required=True, help="Underlying symbol.")
@click.option("--expiry", default=None, help="Expiry YYYY-MM-DD (default: nearest ~45 DTE).")
@click.option("--width", default=8, type=int, help="Strikes to show on each side of spot.")
def options_chain(ticker, expiry, width):
    """Show the near-the-money option chain (strike, bid/ask, last, IV, OI)."""
    ticker = ticker.upper().strip()
    expiry_date = _resolve_expiry(ticker, expiry)
    expiry_iso = expiry_date.isoformat() if expiry_date else None
    chain = get_option_chain(ticker, expiry_iso)
    if chain is None:
        click.secho(f"No option chain available for {ticker}.", fg="red")
        sys.exit(1)

    spot = chain.spot
    click.echo(f"\n{ticker} option chain — expiry {chain.expiry}" + (f" | spot ${spot:,.2f}" if spot else ""))
    for right in ("CALL", "PUT"):
        df = chain.side(right)
        if df is None or df.empty:
            continue
        frame = df.copy()
        if spot and "strike" in frame.columns:
            frame = frame.iloc[(frame["strike"] - spot).abs().argsort()[: width * 2]].sort_values("strike")
        rows = []
        for _, r in frame.iterrows():
            rows.append([
                f"{r.get('strike', float('nan')):.2f}",
                f"{r.get('bid', float('nan')):.2f}",
                f"{r.get('ask', float('nan')):.2f}",
                f"{r.get('lastPrice', float('nan')):.2f}",
                f"{float(r.get('impliedVolatility', 0.0)) * 100:.1f}%",
                f"{int(r.get('openInterest', 0) or 0)}",
            ])
        click.echo(f"\n{right}S")
        click.echo(tabulate(rows, headers=["Strike", "Bid", "Ask", "Last", "IV", "OI"], tablefmt="simple"))


@cli.command("options-analyze")
@click.option("--underlying", "-u", required=True, help="Underlying symbol.")
@click.option("--structure", default=None, help="Named structure, e.g. bull-put-spread, cash-secured-put, long-call.")
@click.option("--strikes", default=None, help="Comma-separated strikes in the structure's documented order.")
@click.option("--leg", "leg_specs", multiple=True, help='Explicit leg: "ACTION RIGHT STRIKE [CONTRACTS]" (repeatable).')
@click.option("--expiry", default=None, help="Expiry YYYY-MM-DD (default: nearest ~45 DTE).")
@click.option("--contracts", default=1, type=int, help="Contracts (hands).")
@click.option("--shares", default=None, type=float, help="Shares backing a covered call.")
@click.option("--secured", default=None, type=click.Choice(["cash", "stock", "short_stock"]), help="How short legs are secured.")
@click.option("--vol", default=None, type=float, help="Override volatility (decimal). Default: chain IV, else realized vol.")
@click.option("--rate", default=None, type=float, help="Override risk-free rate (decimal). Default: ^IRX.")
@click.option("--spot", default=None, type=float, help="Override spot price (default: live).")
@click.option("--american/--european", default=True, help="Exercise style (equity/ETF=American).")
@click.option("--output-dir", default=None, help="Directory for the order ticket + payoff plot.")
def options_analyze(underlying, structure, strikes, leg_specs, expiry, contracts, shares, secured, vol, rate, spot, american, output_dir):
    """Price a Level-2 structure: net debit/credit, max P/L, breakevens, POP, greeks."""
    settings, _ = _load_config()
    underlying = underlying.upper().strip()
    expiry_date = _resolve_expiry(underlying, expiry)
    if expiry_date is None:
        click.secho("Could not resolve an expiry. Pass --expiry YYYY-MM-DD.", fg="red")
        sys.exit(1)

    # Build the strategy from explicit legs or a named structure.
    try:
        if leg_specs:
            legs = [_parse_leg_spec(s, underlying, expiry_date) for s in leg_specs]
            strategy = OptionStrategy(name=structure or "custom", underlying=underlying, legs=legs)
        elif structure and strikes:
            strike_list = [float(s) for s in strikes.split(",") if s.strip()]
            strategy = build_named_strategy(structure, underlying, expiry_date, strike_list, contracts, shares=shares)
        else:
            click.secho("Provide either --leg specs or --structure with --strikes.", fg="red")
            sys.exit(1)
    except ValueError as error:
        click.secho(f"Invalid strategy: {error}", fg="red")
        sys.exit(1)

    if secured:
        strategy.secured_by = {"cash": SECURED_CASH, "stock": SECURED_STOCK, "short_stock": SECURED_SHORT_STOCK}[secured]
    if shares is not None:
        strategy.underlying_shares = shares

    # Resolve market parameters.
    need_chain = spot is None or vol is None
    chain = get_option_chain(underlying, expiry_date.isoformat()) if need_chain else None
    spot_val = spot if spot is not None else ((chain.spot if chain and chain.spot else None) or get_current_price(underlying))
    if not spot_val:
        click.secho("Could not determine spot price. Pass --spot.", fg="red")
        sys.exit(1)
    rate_val = rate if rate is not None else get_risk_free_rate()
    if vol is not None:
        vols = vol
    else:
        vmap = {}
        for leg in strategy.legs:
            iv = _chain_iv(chain, leg.right, leg.strike)
            if iv:
                vmap[leg.strike] = iv
        vols = vmap if vmap else (realized_volatility(underlying) or 0.30)

    violations = validate_level2(strategy)
    analysis = analyze_strategy(strategy, spot_val, rate_val, vols, eval_date=date.today(), american=american)

    ticket_lines = order_ticket_lines(strategy, analysis, spot_val, rate_val, eval_date=date.today())
    for line in ticket_lines:
        click.echo(line)

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports")) / date.today().isoformat()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = strategy.name.lower().replace(" ", "_")
    ticket_path = write_option_ticket(
        strategy, analysis, spot_val, rate_val,
        report_dir / f"option_ticket_{underlying}_{safe}_{stamp}.md",
        eval_date=date.today(),
        title=f"Option Order Ticket — {underlying} {strategy.name}",
        context_notes=[f"Spot ${spot_val:,.2f}", f"Rate {rate_val:.3%}", f"American={american}"],
    )
    plot_path = plot_option_payoff(
        analysis.spot_grid, analysis.payoff,
        report_dir / f"option_payoff_{underlying}_{safe}_{stamp}.png",
        breakevens=analysis.breakevens, spot=spot_val,
        title=f"{underlying} {strategy.name} payoff at expiry",
    )
    if violations:
        click.secho("\nLevel-2 violations: not executable as specified.", fg="red")
    click.echo(f"\nTicket: {ticket_path}")
    click.echo(f"Payoff: {plot_path}")


def _strikes_str(strategy) -> str:
    return "/".join(f"{leg.strike:g}{leg.right[0]}" for leg in strategy.legs)


@cli.command("options-screen")
@click.option("--underlying", "-u", required=True, help="Underlying symbol.")
@click.option("--direction", default="income", type=click.Choice(["income", "bullish"]), help="Thesis: income (sell puts) or bullish (buy calls).")
@click.option("--expiry", default=None, help="Expiry YYYY-MM-DD (default: nearest ~45 DTE).")
@click.option("--width", default=None, type=float, help="Spread width $ (omit for cash-secured put / long call).")
@click.option("--contracts", default=1, type=int, help="Contracts (hands).")
@click.option("--rate", default=None, type=float, help="Override risk-free rate (decimal).")
@click.option("--top", default=5, type=int, help="Number of ranked candidates to show.")
@click.option("--output-dir", default=None, help="Directory for the screen ticket markdown.")
def options_screen(underlying, direction, expiry, width, contracts, rate, top, output_dir):
    """Rank defined-risk option candidates (which put to sell / call to buy) by POP, RoR, EV."""
    settings, _ = _load_config()
    underlying = underlying.upper().strip()
    expiry_date = _resolve_expiry(underlying, expiry)
    if expiry_date is None:
        click.secho("Could not resolve an expiry. Pass --expiry YYYY-MM-DD.", fg="red")
        sys.exit(1)
    chain = get_option_chain(underlying, expiry_date.isoformat())
    if chain is None or not chain.spot:
        click.secho(f"No usable option chain/spot for {underlying}.", fg="red")
        sys.exit(1)
    rate_val = rate if rate is not None else get_risk_free_rate()

    try:
        candidates = screen_chain(chain, direction, rate_val, spread_width=width, contracts=contracts, top=top)
    except ValueError as error:
        click.secho(str(error), fg="red")
        sys.exit(1)
    if not candidates:
        click.secho("No candidates passed the filters (check OTM range / chain liquidity).", fg="yellow")
        return

    click.echo(f"\n{underlying} {direction} screen — expiry {chain.expiry} | spot ${chain.spot:,.2f}")
    rows = []
    for i, c in enumerate(candidates, 1):
        a = c.analysis
        prem = f"+${-a.net_debit:,.0f}" if a.net_debit < 0 else f"-${a.net_debit:,.0f}"
        rows.append([
            i, c.strategy.name, _strikes_str(c.strategy), prem,
            f"{(c.pop or 0) * 100:.0f}%", f"${a.max_loss:,.0f}",
            f"{c.annualized_ror * 100:.0f}%", f"{c.score:.3f}",
        ])
    click.echo(tabulate(
        rows,
        headers=["#", "Structure", "Strikes", "Prem", "POP", "Max loss", "Ann.RoR", "Score"],
        tablefmt="simple",
    ))

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports")) / date.today().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = report_dir / f"options_screen_{underlying}_{direction}_{stamp}.md"
    lines = [f"# Option Screen — {underlying} ({direction})", "",
             f"- Expiry: {chain.expiry} | Spot: ${chain.spot:,.2f} | Rate: {rate_val:.3%}",
             "- Ranked, fully-specified, Level-2 defined-risk candidates. Confirm against the live chain.", ""]
    for i, c in enumerate(candidates, 1):
        lines.append(f"## Candidate {i} (score {c.score:.3f})")
        lines.append("")
        lines.extend(order_ticket_lines(c.strategy, c.analysis, chain.spot, rate_val, eval_date=date.today()))
    out_path.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"\nScreen written to: {out_path}")


# ── OPTIONS LOG & MONITOR ─────────────────────────────────────────────────────

def _option_logger(settings):
    return OptionTradeLogger(
        settings.get("options_history_file", "data/options_history.json"),
        settings.get("options_positions_file", "data/options_positions.json"),
    )


@cli.command("log-option")
@click.option("--underlying", "-u", required=True, help="Underlying symbol.")
@click.option("--structure", required=True, help="Named structure, e.g. bull-put-spread, cash-secured-put.")
@click.option("--strikes", required=True, help="Comma-separated strikes in the structure's documented order.")
@click.option("--expiry", required=True, help="Expiry YYYY-MM-DD.")
@click.option("--contracts", default=1, type=int, help="Contracts (hands).")
@click.option("--net-debit", "net_debit", required=True, type=float, help="Position $: + debit paid, - credit received.")
@click.option("--secured", default=None, type=click.Choice(["cash", "stock", "short_stock"]), help="How short legs are secured.")
@click.option("--shares", default=None, type=float, help="Shares backing a covered call.")
@click.option("--take-profit", default=None, type=float, help="Take-profit fraction of risk base (e.g. 0.5).")
@click.option("--stop-loss", default=None, type=float, help="Stop-loss fraction of risk base.")
@click.option("--close-by-dte", default=21, type=int, help="Time-stop DTE.")
@click.option("--rationale", "-r", default="", help="Why this trade.")
@click.option("--tags", default="", help="Comma-separated tags.")
def log_option(underlying, structure, strikes, expiry, contracts, net_debit, secured, shares, take_profit, stop_loss, close_by_dte, rationale, tags):
    """Record an opened option position (must be Level-2 compliant)."""
    settings, _ = _load_config()
    underlying = underlying.upper().strip()
    try:
        expiry_date = date.fromisoformat(expiry)
        strike_list = [float(s) for s in strikes.split(",") if s.strip()]
        strategy = build_named_strategy(structure, underlying, expiry_date, strike_list, contracts, shares=shares)
    except ValueError as error:
        click.secho(f"Invalid option: {error}", fg="red")
        sys.exit(1)
    if secured:
        strategy.secured_by = {"cash": SECURED_CASH, "stock": SECURED_STOCK, "short_stock": SECURED_SHORT_STOCK}[secured]
    if shares is not None:
        strategy.underlying_shares = shares

    violations = validate_level2(strategy)
    if violations:
        click.secho("Refusing to log a non-Level-2 position:", fg="red")
        for v in violations:
            click.echo(f"  - {v}")
        sys.exit(1)

    logger = _option_logger(settings)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    position = logger.log_open(
        strategy, net_debit, rationale=rationale, tags=tag_list,
        take_profit_pct=take_profit, stop_loss_pct=stop_loss, close_by_dte=close_by_dte,
    )
    kind = "credit" if position.is_credit else "debit"
    click.secho(f"Logged {position.id} ({kind} {_dollars(abs(net_debit))}).", fg="green")


def _dollars(value: float) -> str:
    return f"${value:,.2f}"


@cli.command()
@click.option("--snapshot", "snapshot_path", default=None, help="Snapshot for sleeve/basket context (default: latest).")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml", help="Sleeve universe for basket bands.")
@click.option("--rate", default=None, type=float, help="Override risk-free rate (decimal).")
@click.option("--vol-window", default=30, type=int, help="Trailing days for realized-vol marks.")
@click.option("--output-dir", default=None, help="Directory for the monitor report.")
def monitor(snapshot_path, universe_path, rate, vol_window, output_dir):
    """Re-mark open options, evaluate exit/roll/assignment/event rules, and write alerts."""
    settings, _ = _load_config()
    logger = _option_logger(settings)
    positions = logger.load_open_positions()

    rate_val = rate if rate is not None else get_risk_free_rate()
    events = load_event_calendar(settings.get("event_calendar_file", "config/event_calendar.yaml"))

    monitors = []
    risk = None
    stress = None
    spot_map: dict[str, float] = {}
    vol_map: dict[str, float] = {}
    if positions:
        underlyings = sorted({p.underlying for p in positions})
        fetched = get_current_prices(underlyings)
        spot_map = {u: v for u, v in fetched.items() if v}
        vol_map = {u: (realized_volatility(u, vol_window) or 0.30) for u in underlyings}
        monitors = monitor_positions(positions, spot_map, rate_val, vol_map, events=events)
        strategies = [p.strategy for p in positions]
        risk = aggregate_greeks(strategies, spot_map, rate_val, vol_map)
        stress = stress_test(strategies, spot_map, rate_val, vol_map)

    # Basket drift + options sleeve context from the latest snapshot.
    sleeve = None
    basket_alerts: list[str] = []
    tracker = PortfolioTracker(snapshots_dir=settings.get("snapshots_dir", "data/portfolio_snapshots"))
    snapshot = tracker.load_snapshot(snapshot_path) if snapshot_path else tracker.load_latest_snapshot()
    if snapshot is not None:
        try:
            _, sleeves = load_sleeve_universe(universe_path)
        except (FileNotFoundError, ValueError, KeyError):
            sleeves = []
        view = build_baskets(snapshot, sleeves)
        for b in view.baskets:
            status = b.band_status()
            if status in ("ABOVE", "BELOW"):
                basket_alerts.append(
                    f"{b.name}: {b.weight_in_portfolio_pct:.1f}% is {status} the "
                    f"{b.band_min_pct:.0f}-{b.band_max_pct:.0f}% band."
                )
        options_value = risk.gross_market_value if risk else 0.0
        portfolio_value = snapshot.total_portfolio_value + options_value
        sleeve = options_sleeve_status(options_value, portfolio_value)

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports")) / date.today().isoformat()
    out_path = report_dir / f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    context_notes = [f"Rate {rate_val:.3%}", f"Open positions: {len(positions)}"]
    write_monitor_report(
        monitors, out_path, risk=risk, stress=stress, sleeve=sleeve,
        basket_alerts=basket_alerts, context_notes=context_notes,
    )

    # Console summary.
    alerts = all_alerts(monitors)
    if not positions:
        click.secho("No open option positions to monitor.", fg="yellow")
    if alerts:
        click.echo(f"\n{len(alerts)} alert(s):")
        rows = [[a.severity, a.kind, a.underlying, a.message] for a in alerts]
        click.echo(tabulate(rows, headers=["Severity", "Kind", "Underlying", "Message"], tablefmt="simple"))
    elif positions:
        click.secho("No position alerts triggered.", fg="green")
    for ba in basket_alerts:
        click.secho(f"Basket: {ba}", fg="yellow")
    click.echo(f"\nMonitor report: {out_path}")


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
              type=click.Choice(["trade", "review", "risk", "options"]),
              help="Prompt type: trade (default), review, risk, or options.")
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

    # Basket context (offline) and best-effort options context for the prompt.
    basket_rows = None
    option_rows = None
    risk_summary = None
    try:
        _, prompt_sleeves = load_sleeve_universe("config/growth_universe.yaml")
        prompt_view = build_baskets(snap, prompt_sleeves)
        if prompt_view.baskets:
            basket_rows = compute_basket_metrics(prompt_view)
    except (FileNotFoundError, ValueError, KeyError):
        pass
    try:
        open_positions = _option_logger(settings).load_open_positions()
        if open_positions:
            underlyings = sorted({p.underlying for p in open_positions})
            spot_map = {u: v for u, v in get_current_prices(underlyings).items() if v}
            vol_map = {u: (realized_volatility(u) or 0.30) for u in underlyings}
            rate_val = get_risk_free_rate()
            monitors = monitor_positions(open_positions, spot_map, rate_val, vol_map)
            option_rows = [
                {"underlying": m.underlying, "structure": m.structure, "mark": m.mark,
                 "pnl": m.pnl, "pnl_pct": m.pnl_pct * 100, "dte": m.dte}
                for m in monitors
            ]
            book_risk = aggregate_greeks([p.strategy for p in open_positions], spot_map, rate_val, vol_map)
            sleeve = options_sleeve_status(
                book_risk.gross_market_value,
                snap.total_portfolio_value + book_risk.gross_market_value,
            )
            risk_summary = {
                "net_delta": book_risk.net_delta, "dollar_delta": book_risk.dollar_delta,
                "net_theta": book_risk.net_theta, "net_vega": book_risk.net_vega,
                "sleeve_weight_pct": sleeve["weight_pct"], "sleeve_status": sleeve["status"],
            }
    except Exception:  # best-effort enrichment; never block prompt generation on market data
        option_rows = option_rows
        risk_summary = risk_summary

    try:
        rendered = generate_prompt(
            prompt_type=prompt_type,
            snapshot=snap,
            recent_trades=recent_trades,
            metrics=metrics,
            persistent_ctx=persistent_ctx,
            user_question=user_question,
            analysis_bundle=analysis_bundle,
            basket_rows=basket_rows,
            option_rows=option_rows,
            risk_summary=risk_summary,
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


# ── QUANT SUITE ───────────────────────────────────────────────────────────────

def _quant_report_dir(output_dir, settings):
    """Resolve and create the dated quant artifact directory."""
    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports")) / date.today().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _resolve_tickers(tickers, universe_path):
    """Tickers from a comma list, else the sleeve proxies from the universe."""
    if tickers:
        return [t.strip().upper() for t in tickers.split(",") if t.strip()]
    _settings, sleeves = load_sleeve_universe(universe_path)
    return [s.proxy.upper() for s in sleeves]


def _parse_int_list(text):
    return [int(x.strip()) for x in str(text).split(",") if x.strip()]


@cli.command("signals")
@click.option("--tickers", default=None, help="Comma-separated tickers (default: sleeve proxies).")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml")
@click.option("--benchmark", default="SPY", help="Benchmark for relative strength.")
@click.option("--period", default="2y", help="Yahoo Finance history period.")
@click.option("--interval", default="1d", type=click.Choice(["1d", "1wk"]))
@click.option("--output-dir", default=None)
def signals(tickers, universe_path, benchmark, period, interval, output_dir):
    """Compute per-ticker technical SignalSets and write a signals report."""
    settings, _ = _load_config()
    ticker_list = _resolve_tickers(tickers, universe_path)
    click.echo(f"Computing signals for {len(ticker_list)} tickers ({period} {interval})...")
    sets = compute_universe_signals(ticker_list, period=period, interval=interval, benchmark=benchmark)

    rows = []
    for s in sets:
        comp = s.composite or {}
        rows.append([
            s.ticker,
            f"{s.close:,.2f}" if s.close is not None else "n/a",
            f"{comp.get('overall'):.2f}" if comp.get("overall") is not None else "n/a",
            f"{(s.momentum or {}).get('rsi'):.0f}" if (s.momentum or {}).get("rsi") is not None else "n/a",
            ", ".join(s.flags) if s.flags else "-",
        ])
    click.echo(tabulate(rows, headers=["Ticker", "Close", "Score", "RSI", "Flags"], tablefmt="simple"))

    report_dir = _quant_report_dir(output_dir, settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = quant_reporting.write_signals_report(sets, report_dir / f"signals_{stamp}.md")
    click.echo(f"\nGenerated: {path}")


@cli.command("backtest")
@click.option("--strategy", default="momentum", type=click.Choice(["equal-weight", "momentum", "sleeve"]))
@click.option("--tickers", default=None, help="Comma list (equal-weight/momentum); defaults to sleeve proxies.")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml")
@click.option("--method", default="erc", type=click.Choice(METHODS), help="Sleeve strategy weighting method.")
@click.option("--lookback", default=26, type=int, help="Momentum lookback (periods).")
@click.option("--top-k", default=3, type=int, help="Momentum: number of holdings.")
@click.option("--period", default="5y")
@click.option("--interval", default="1wk", type=click.Choice(["1d", "1wk", "1mo"]))
@click.option("--rebalance", default="M", type=click.Choice(["D", "W", "M", "Q"]))
@click.option("--cost-bps", default=5.0, type=float)
@click.option("--benchmark", default="SPY")
@click.option("--walk-forward", "do_walk_forward", is_flag=True, default=False)
@click.option("--train", default=104, type=int, help="Walk-forward train window (periods).")
@click.option("--test", default=26, type=int, help="Walk-forward test window (periods).")
@click.option("--output-dir", default=None)
def backtest(strategy, tickers, universe_path, method, lookback, top_k, period, interval,
             rebalance, cost_bps, benchmark, do_walk_forward, train, test, output_dir):
    """Backtest a strategy (optionally walk-forward) vs a benchmark."""
    settings, _ = _load_config()
    ppy = PERIODS_PER_YEAR[interval]
    universe_settings, sleeves = load_sleeve_universe(universe_path)

    if strategy == "sleeve":
        ticker_list = [s.proxy.upper() for s in sleeves]
    else:
        ticker_list = _resolve_tickers(tickers, universe_path)

    fetch = sorted(set(ticker_list + [benchmark.upper()]))
    click.echo(f"Fetching {period} {interval} history for {len(fetch)} symbols...")
    history = get_price_history(fetch, period=period, interval=interval)
    if history.empty:
        click.secho("No price history returned.", fg="red")
        sys.exit(1)
    bench_series = history[benchmark.upper()] if benchmark.upper() in history.columns else None
    price_df = history[[c for c in ticker_list if c in history.columns]]
    if price_df.empty:
        click.secho("None of the requested tickers had history.", fg="red")
        sys.exit(1)

    min_obs = min(52, max(8, train // 2))

    def make_strategy(_train_df=None):
        if strategy == "equal-weight":
            return EqualWeightStrategy()
        if strategy == "momentum":
            return MomentumStrategy(lookback=lookback, top_k=top_k)
        return SleeveRebalanceStrategy(
            sleeves, method=method, periods_per_year=ppy, min_observations=min_obs,
            cash_target_pct=float(universe_settings.get("cash_target_pct", 10.0)),
        )

    report_dir = _quant_report_dir(output_dir, settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if do_walk_forward:
        wf = walk_forward(make_strategy, price_df, train=train, test=test,
                          rebalance=rebalance, cost_bps=cost_bps, periods_per_year=ppy)
        _echo_metrics("Walk-forward OOS", wf.metrics)
        md = quant_reporting.write_walk_forward_report(wf, report_dir / f"backtest_wf_{strategy}_{stamp}.md")
        png = quant_reporting.plot_equity_curve(wf.stitched_equity, report_dir / f"backtest_wf_{strategy}_{stamp}.png",
                                                title=f"Walk-forward OOS: {strategy}")
    else:
        result = BacktestEngine().run(make_strategy(), price_df, rebalance=rebalance,
                                      cost_bps=cost_bps, periods_per_year=ppy, benchmark=bench_series)
        _echo_metrics(result.strategy, result.metrics, result.benchmark_metrics)
        md = quant_reporting.write_backtest_report(result, report_dir / f"backtest_{strategy}_{stamp}.md")
        png = quant_reporting.plot_equity_curve(result.equity_curve, report_dir / f"backtest_{strategy}_{stamp}.png",
                                                benchmark=result.benchmark_curve, title=f"Backtest: {strategy}")
    click.echo(f"\nGenerated: {md}")
    if png:
        click.echo(f"Generated: {png}")


def _echo_metrics(label, metrics, benchmark=None):
    row = metrics.as_row()
    headers = ["Metric", label]
    data = [
        ["CAGR", f"{row['cagr_pct']:.2f}%"],
        ["Ann. vol", f"{row['ann_volatility_pct']:.2f}%"],
        ["Sharpe", "n/a" if row["sharpe"] is None else f"{row['sharpe']:.2f}"],
        ["Sortino", "n/a" if row["sortino"] is None else f"{row['sortino']:.2f}"],
        ["Max DD", f"{row['max_drawdown_pct']:.2f}%"],
        ["Calmar", "n/a" if row["calmar"] is None else f"{row['calmar']:.2f}"],
    ]
    if benchmark is not None:
        brow = benchmark.as_row()
        headers.append("Benchmark")
        data[0].append(f"{brow['cagr_pct']:.2f}%")
        data[1].append(f"{brow['ann_volatility_pct']:.2f}%")
        data[2].append("n/a" if brow["sharpe"] is None else f"{brow['sharpe']:.2f}")
        data[3].append("n/a" if brow["sortino"] is None else f"{brow['sortino']:.2f}")
        data[4].append(f"{brow['max_drawdown_pct']:.2f}%")
        data[5].append("n/a" if brow["calmar"] is None else f"{brow['calmar']:.2f}")
    click.echo("\n" + tabulate(data, headers=headers, tablefmt="simple"))


@cli.command("optimize-params")
@click.option("--tickers", default=None, help="Comma list (default: sleeve proxies).")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml")
@click.option("--lookbacks", default="13,26,52", help="Momentum lookback grid.")
@click.option("--top-ks", default="1,2,3", help="Momentum top-k grid.")
@click.option("--period", default="6y")
@click.option("--interval", default="1wk", type=click.Choice(["1d", "1wk", "1mo"]))
@click.option("--rebalance", default="M", type=click.Choice(["D", "W", "M", "Q"]))
@click.option("--cost-bps", default=5.0, type=float)
@click.option("--scorer", default="sharpe", type=click.Choice(["sharpe", "sortino", "cagr", "calmar"]))
@click.option("--train", default=104, type=int)
@click.option("--test", default=26, type=int)
@click.option("--output-dir", default=None)
def optimize_params(tickers, universe_path, lookbacks, top_ks, period, interval,
                    rebalance, cost_bps, scorer, train, test, output_dir):
    """Walk-forward grid search over momentum params (reports IS vs OOS)."""
    settings, _ = _load_config()
    ppy = PERIODS_PER_YEAR[interval]
    ticker_list = _resolve_tickers(tickers, universe_path)
    click.echo(f"Fetching {period} {interval} history for {len(ticker_list)} symbols...")
    price_df = get_price_history(ticker_list, period=period, interval=interval)
    if price_df.empty:
        click.secho("No price history returned.", fg="red")
        sys.exit(1)

    space = ParamSpace({"lookback": _parse_int_list(lookbacks), "top_k": _parse_int_list(top_ks)})
    factory = lambda params: MomentumStrategy(lookback=params["lookback"], top_k=params["top_k"])
    try:
        wfo = walk_forward_optimize(factory, price_df, space, train=train, test=test,
                                    rebalance=rebalance, cost_bps=cost_bps, scorer=scorer,
                                    periods_per_year=ppy)
    except ValueError as error:
        click.secho(f"Optimization error: {error}", fg="red")
        sys.exit(1)

    click.echo(f"\nScorer: {scorer} | folds: {len(wfo.folds)}")
    click.echo(f"Mean IS: {wfo.mean_is_score:.3f} | Mean OOS: {wfo.mean_oos_score:.3f} | gap: {wfo.is_oos_gap:.3f}")
    click.secho(
        "OVERFIT WARNING: in-sample edge does not hold out of sample." if wfo.overfit_warning
        else "No strong overfit signal.",
        fg="yellow" if wfo.overfit_warning else "green",
    )
    _echo_metrics("Stitched OOS", wfo.oos_metrics)
    report_dir = _quant_report_dir(output_dir, settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = quant_reporting.write_optimize_report(wfo, report_dir / f"optimize_params_{stamp}.md")
    png = quant_reporting.plot_equity_curve(wfo.stitched_equity, report_dir / f"optimize_params_{stamp}.png",
                                            title="Walk-forward OOS (optimized)")
    click.echo(f"\nGenerated: {md}")
    if png:
        click.echo(f"Generated: {png}")


@cli.command("factor-report")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml")
@click.option("--period", default="3y")
@click.option("--interval", default="1wk", type=click.Choice(["1d", "1wk", "1mo"]))
@click.option("--output-dir", default=None)
def factor_report(universe_path, period, interval, output_dir):
    """Factor exposures (betas), fit, and equal-weight variance decomposition."""
    settings, _ = _load_config()
    ppy = PERIODS_PER_YEAR[interval]
    _universe_settings, sleeves = load_sleeve_universe(universe_path)
    assets = [s.proxy.upper() for s in sleeves]
    proxy_by_factor = {k: v.upper() for k, v in DEFAULT_FACTOR_PROXIES.items()}
    factor_tickers = list(proxy_by_factor.values())

    fetch = sorted(set(assets + factor_tickers))
    click.echo(f"Fetching {period} {interval} history for {len(fetch)} symbols...")
    history = get_price_history(fetch, period=period, interval=interval)
    if history.empty:
        click.secho("No price history returned.", fg="red")
        sys.exit(1)

    rets = history.pct_change().dropna(how="all")
    asset_cols = [a for a in assets if a in rets.columns]
    asset_returns = rets[asset_cols]
    factor_returns = pd.DataFrame({
        factor: rets[ticker] for factor, ticker in proxy_by_factor.items() if ticker in rets.columns
    })
    try:
        model = build_factor_model(asset_returns, factor_returns, periods_per_year=ppy)
    except ValueError as error:
        click.secho(f"Factor model error: {error}", fg="red")
        sys.exit(1)

    weights = pd.Series(1.0 / len(asset_cols), index=asset_cols)  # equal-weight assumption
    decomposition = variance_decomposition(weights, model)

    click.echo(f"\nFactors: {', '.join(model.factors)}")
    click.echo(f"Systematic variance: {decomposition['systematic_pct']:.1f}% | "
               f"Specific: {decomposition['specific_pct']:.1f}%")
    rows = [[a] + [f"{model.exposures.loc[a, f]:.2f}" for f in model.factors] + [f"{model.r_squared[a]:.2f}"]
            for a in model.exposures.index]
    click.echo(tabulate(rows, headers=["Asset", *model.factors, "R2"], tablefmt="simple"))

    report_dir = _quant_report_dir(output_dir, settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = quant_reporting.write_factor_report(model, model.exposures, decomposition,
                                             report_dir / f"factor_report_{stamp}.md")
    click.echo(f"\nGenerated: {md}")


@cli.command("options-backtest")
@click.option("--ticker", "-u", required=True)
@click.option("--structure", default="cash-secured-put",
              type=click.Choice(["cash-secured-put", "bull-put-spread"]))
@click.option("--dte", default=30, type=int)
@click.option("--otm", default=0.05, type=float, help="Short strike fraction OTM.")
@click.option("--width-pct", default=0.05, type=float, help="Spread width as fraction of spot.")
@click.option("--take-profit-pct", default=50.0, type=float)
@click.option("--stop-loss-pct", default=100.0, type=float)
@click.option("--iv-rv-premium", default=1.2, type=float)
@click.option("--period", default="3y")
@click.option("--rate", default=None, type=float)
@click.option("--output-dir", default=None)
def options_backtest(ticker, structure, dte, otm, width_pct, take_profit_pct, stop_loss_pct,
                     iv_rv_premium, period, rate, output_dir):
    """Backtest a defined-risk option structure on reconstructed theoretical prices."""
    settings, _ = _load_config()
    cfg = OptionBacktestConfig(
        structure=structure, dte=dte, otm=otm, width_pct=width_pct,
        take_profit_pct=take_profit_pct, stop_loss_pct=stop_loss_pct, iv_rv_premium=iv_rv_premium,
    )
    click.echo(f"Backtesting {structure} on {ticker.upper()} ({period}, {dte} DTE)...")
    result = backtest_option_structure(ticker, cfg, period=period, rate=rate)
    s = result.summary
    if not s.get("n_trades"):
        click.secho("No trades generated (insufficient history or no usable entries).", fg="yellow")
        return

    click.echo(tabulate([
        ["Trades", s["n_trades"]],
        ["Win rate", f"{s['win_rate_pct']:.1f}%"],
        ["Total P&L", f"${s['total_pnl']:,.2f}"],
        ["Total return", f"{s['total_return_pct']:.2f}%"],
        ["Avg credit", f"${s['avg_credit']:,.2f}"],
        ["Avg return on risk", f"{s['avg_return_on_risk_pct']:.2f}%"],
        ["Worst trade", f"${s['worst_trade']:,.2f}"],
    ], headers=["Metric", "Value"], tablefmt="simple"))
    click.secho(f"\n{result.note}", fg="cyan")

    report_dir = _quant_report_dir(output_dir, settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = quant_reporting.write_options_backtest_report(
        result, report_dir / f"options_backtest_{ticker.upper()}_{structure}_{stamp}.md")
    png = quant_reporting.plot_equity_curve(result.equity_curve,
                                            report_dir / f"options_backtest_{ticker.upper()}_{structure}_{stamp}.png",
                                            title=f"{ticker.upper()} {structure} equity")
    click.echo(f"\nGenerated: {md}")
    if png:
        click.echo(f"Generated: {png}")


# ── DAILY ADVISORY (Phase 1 orchestrator) ────────────────────────────────────

def _advisory_metrics(tracker, notes):
    """Deterministic headline metrics from saved snapshots (degrade gracefully)."""
    try:
        snaps = select_latest_snapshot_per_day([tracker.load_snapshot(p) for p in tracker.list_snapshots()])
        if len(snaps) < 2:
            return {}
        m = compute_metrics(snaps)
        return {
            "cumulative_return_pct": m.cumulative_return_pct,
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown_pct,
            "concentration_top3_pct": m.concentration_top3_pct,
        }
    except Exception as error:  # noqa: BLE001
        notes.append(f"Metrics skipped: {error}")
        return {}


def _advisory_open_option_alerts(settings, rate, notes):
    """Re-mark open option positions and collect monitor alerts (network)."""
    alerts = []
    try:
        logger = OptionTradeLogger(
            settings.get("options_history_file", "data/options_history.json"),
            settings.get("options_positions_file", "data/options_positions.json"),
        )
        open_positions = logger.load_open_positions()
        if not open_positions:
            return alerts
        rate_val = rate if rate is not None else get_risk_free_rate()
        underlyings = sorted({p.underlying for p in open_positions})
        spot_map = {k: v for k, v in get_current_prices(underlyings).items() if v}
        vol_map = {u: (realized_volatility(u) or 0.0) for u in underlyings}
        monitors = monitor_positions(open_positions, spot_map, rate_val, vol_map)
        for al in all_alerts(monitors):
            alerts.append({"underlying": al.underlying, "kind": al.kind,
                           "severity": al.severity, "message": al.message})
    except Exception as error:  # noqa: BLE001
        notes.append(f"Option monitor skipped: {error}")
    return alerts


def _advisory_signals(snapshot, ctx, notes):
    """Technical SignalSets for held tickers (one batched download)."""
    try:
        held = sorted({p.ticker.upper() for p in snapshot.positions})
        if not held:
            return {}
        benchmark = getattr(ctx, "benchmark", None) or "SPY"
        sets = compute_universe_signals(held, period="1y", interval="1d", benchmark=benchmark)
        return {s.ticker: s for s in sets if s.close is not None}
    except Exception as error:  # noqa: BLE001
        notes.append(f"Signals skipped: {error}")
        return {}


def _advisory_screens(underlyings, rate, notes):
    """Top defined-risk income candidate per underlying (network)."""
    candidates = []
    try:
        rate_val = rate if rate is not None else get_risk_free_rate()
        for underlying in underlyings:
            chain = get_option_chain(underlying)
            if chain is None:
                continue
            ranked = screen_chain(chain, direction="income", rate=rate_val, top=1)
            if ranked:
                c = ranked[0]
                candidates.append({
                    "underlying": underlying,
                    "structure": c.strategy.name,
                    "summary": f"short {c.short_strike:.0f}, POP {c.pop:.0%}, "
                               f"ann RoR {c.annualized_ror:.0%}",
                })
    except Exception as error:  # noqa: BLE001
        notes.append(f"Option screens skipped: {error}")
    return candidates


@cli.command("daily-advisory")
@click.option("--date", "brief_date_text", default=None, help="Run date YYYY-MM-DD (default: snapshot date).")
@click.option("--snapshot", "snapshot_path", default=None, help="Snapshot to brief against (default: latest).")
@click.option("--universe", "universe_path", default="config/growth_universe.yaml")
@click.option("--thesis-file", default=None, help="Boist markdown override (default: latest by date).")
@click.option("--data-dir", default="data")
@click.option("--event-horizon-days", default=30, type=int)
@click.option("--include-option-screens", is_flag=True, default=False)
@click.option("--screen-underlying", "screen_underlyings", default=None, help="Comma list to screen.")
@click.option("--rate", default=None, type=float)
@click.option("--no-network", is_flag=True, default=False, help="Skip all yfinance calls.")
@click.option("--with-prompt/--no-prompt", default=True)
@click.option("--output-dir", default=None)
def daily_advisory(brief_date_text, snapshot_path, universe_path, thesis_file, data_dir,
                   event_horizon_days, include_option_screens, screen_underlyings, rate,
                   no_network, with_prompt, output_dir):
    """One dated, gated, prioritized advisory packet (markdown + JSON). Read-only."""
    settings, ctx = _load_config()
    tracker = PortfolioTracker(snapshots_dir=settings.get("snapshots_dir", "data/portfolio_snapshots"))
    try:
        snapshot = tracker.load_snapshot(snapshot_path) if snapshot_path else tracker.load_latest_snapshot()
    except FileNotFoundError:
        snapshot = None
    if snapshot is None:
        click.secho("No snapshot available. Run `sync` / `sync-bundle` first.", fg="red")
        sys.exit(1)

    as_of = datetime.strptime(brief_date_text, "%Y-%m-%d").date() if brief_date_text else snapshot.timestamp.date()
    try:
        _universe_settings, sleeves = load_sleeve_universe(universe_path)
    except (FileNotFoundError, ValueError, KeyError):
        sleeves = []

    notes = []
    metrics = _advisory_metrics(tracker, notes)
    gate = options_gate_status(ctx, snapshot.total_portfolio_value)

    # Thesis preview (offline) to seed screen defaults and the prompt question.
    thesis_preview = build_thesis_context(
        as_of, data_dir=data_dir, explicit_file=thesis_file,
        snapshot_date=snapshot.timestamp.date(), known_tickers=known_tickers(sleeves, snapshot),
    )

    open_alerts, candidates, signals_by_ticker = [], [], {}
    if no_network:
        notes.append("Network calls skipped (--no-network): no live option marks, screens, or signals.")
    else:
        open_alerts = _advisory_open_option_alerts(settings, rate, notes)
        signals_by_ticker = _advisory_signals(snapshot, ctx, notes)
        if include_option_screens:
            held = {p.ticker.upper() for p in snapshot.positions}
            if screen_underlyings:
                screen_list = [t.strip().upper() for t in screen_underlyings.split(",") if t.strip()]
            else:
                screen_list = [t for t in thesis_preview.tickers if t in held][:3]
            candidates = _advisory_screens(screen_list, rate, notes)

    option_summary = OptionAdvisorySummary(
        gated=not gate.executable, gate_reason=gate.reason,
        candidates=candidates, open_position_alerts=open_alerts,
        note="Option ideas carry the gate label above." if (candidates or open_alerts)
        else "No live option data (run with --include-option-screens online).",
    )

    run = build_advisory_run(
        as_of=as_of, snapshot=snapshot, snapshot_path=snapshot_path or "latest",
        ctx=ctx, sleeves=sleeves, generated_at=datetime.now().isoformat(timespec="seconds"),
        data_dir=data_dir, thesis_file=thesis_file, event_horizon_days=event_horizon_days,
        option_summary=option_summary, metrics=metrics, signals_by_ticker=signals_by_ticker,
        extra_notes=notes,
    )

    if with_prompt:
        try:
            trades = TradeHistory(TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))).get_recent(10)
            question = run.thesis.digest[:500] if run.thesis.found else "Plan tomorrow's actions."
            prompt_text = truncate_to_budget(
                generate_prompt("trade", snapshot, trades, None, ctx, user_question=question), 4000)
            prompt_dir = Path(settings.get("output_dir", "output/prompts"))
            prompt_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = prompt_dir / f"daily_advisory_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            prompt_path.write_text(prompt_text, encoding="utf-8")
            run.prompt_path = str(prompt_path)
        except Exception as error:  # noqa: BLE001
            run.notes.append(f"Prompt generation skipped: {error}")

    report_dir = Path(output_dir or settings.get("reports_dir", "output/reports")) / as_of.isoformat()
    md_path, json_path = write_advisory(run, report_dir / f"daily_advisory_{as_of.isoformat()}.md",
                                        report_dir / f"daily_advisory_{as_of.isoformat()}.json")

    state = "EXECUTABLE" if gate.executable else "ADVISORY ONLY (gated)"
    click.echo(f"\nDaily advisory for {as_of.isoformat()}")
    click.echo(f"Portfolio: ${snapshot.total_portfolio_value:,.2f} | Cash: {run.cash_pct:.1f}%")
    click.secho(f"OPTIONS GATE: {state} - {gate.reason}", fg="yellow" if not gate.executable else "green")
    actions = [a for a in run.rule_alerts if a.severity == "ACTION"]
    if actions:
        click.echo(tabulate([[a.category, a.title] for a in actions],
                            headers=["Category", "Action item"], tablefmt="simple"))
    else:
        click.echo("No action-level rule breaches.")
    for note in run.notes:
        click.secho(f"  note: {note}", fg="cyan")
    click.echo(f"\nGenerated:\n  {md_path}\n  {json_path}")
    if run.prompt_path:
        click.echo(f"  {run.prompt_path}")


if __name__ == "__main__":
    cli()
