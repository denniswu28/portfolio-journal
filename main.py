"""
main.py - CLI entry point for the Investopedia Portfolio Tracker.

Commands:
  sync --paste          Paste portfolio text interactively
  sync --file FILE      Load portfolio from a .txt file
  sync --csv FILE       Load portfolio from a CSV file
  status                Show current portfolio summary
  analytics             Show performance metrics
  log-trade             Log a new trade with rationale
  history               Show recent trade history
  prompt                Generate an LLM prompt
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from tabulate import tabulate

# ── PATH SETUP ────────────────────────────────────────────────────────────────
# Allow running `python main.py` from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from src.data_ingestion.csv_loader import CSVLoader
from src.data_ingestion.models import PersistentContext
from src.data_ingestion.paste_parser import PasteParser
from src.portfolio.analytics import compute_metrics
from src.portfolio.tracker import PortfolioTracker
from src.prompt_engine.formatter import estimate_tokens, truncate_to_budget
from src.prompt_engine.prompt_builder import generate_prompt
from src.trade_log.history import TradeHistory
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


@click.group()
def cli():
    """Investopedia Portfolio Tracker — parse, analyse, and generate LLM prompts."""


# ── SYNC ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--paste", "mode", flag_value="paste", default=True, help="Paste portfolio text interactively.")
@click.option("--file", "mode", flag_value="file", help="Load from a .txt file.")
@click.option("--csv", "mode", flag_value="csv", help="Load from a CSV file.")
@click.option("--input-file", "-i", default=None, help="Path to input file (for --file and --csv modes).")
@click.option("--cash", default=None, type=float, help="Cash balance override (CSV mode only).")
@click.option("--refresh-prices", is_flag=True, default=False, help="Fetch live prices from yfinance.")
@click.option("--save/--no-save", default=True, help="Save snapshot to disk.")
def sync(mode, input_file, cash, refresh_prices, save):
    """Sync portfolio from paste, file, or CSV and save a snapshot."""
    settings, _ = _load_config()

    parser = PasteParser()
    csv_loader = CSVLoader()

    if mode == "paste":
        click.echo("Paste your Investopedia portfolio text below.")
        click.echo("When done, press Ctrl+D (Unix) or Ctrl+Z then Enter (Windows):")
        click.echo("─" * 60)
        try:
            raw_text = sys.stdin.read()
        except KeyboardInterrupt:
            click.echo("\nAborted.")
            return
        try:
            raw_data = parser.parse(raw_text)
        except ValueError as e:
            click.secho(f"Error parsing text: {e}", fg="red")
            sys.exit(1)

    elif mode == "file":
        if not input_file:
            click.secho("--input-file is required with --file mode.", fg="red")
            sys.exit(1)
        try:
            raw_data = parser.parse_from_file(input_file)
        except (FileNotFoundError, ValueError) as e:
            click.secho(f"Error: {e}", fg="red")
            sys.exit(1)

    elif mode == "csv":
        if not input_file:
            click.secho("--input-file is required with --csv mode.", fg="red")
            sys.exit(1)
        try:
            raw_data = csv_loader.load(input_file, cash=cash or 0.0)
        except (FileNotFoundError, ValueError) as e:
            click.secho(f"Error: {e}", fg="red")
            sys.exit(1)
    else:
        click.secho("Unknown mode. Use --paste, --file, or --csv.", fg="red")
        sys.exit(1)

    snapshots_dir = settings.get("snapshots_dir", "data/portfolio_snapshots")
    tracker = PortfolioTracker(snapshots_dir=snapshots_dir, refresh_prices=refresh_prices)

    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trades = trade_logger.load_all()

    snapshot = tracker.build_snapshot(raw_data, trade_history=trades)

    if save:
        path = tracker.save_snapshot(snapshot)
        click.secho(f"Snapshot saved: {path}", fg="green")

    # Show brief summary
    click.echo(f"\nPortfolio Value: ${snapshot.total_portfolio_value:,.2f}")
    click.echo(f"Cash:            ${snapshot.cash:,.2f}")
    click.echo(f"Cumulative Return: {snapshot.cumulative_return_pct:+.2f}%")
    click.echo(f"Positions: {len(snapshot.positions)}")


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

    snapshot_files = tracker.list_snapshots()
    if len(snapshot_files) < 2:
        click.secho(
            "Need at least 2 snapshots for metrics. Run 'sync' more than once.",
            fg="yellow",
        )
        # Show current snapshot stats if available
        snap = tracker.load_latest_snapshot()
        if snap:
            click.echo(f"\nLatest snapshot: {snap.timestamp.strftime('%Y-%m-%d %H:%M')}")
            click.echo(f"Portfolio Value: ${snap.total_portfolio_value:,.2f}")
        return

    snapshots = [tracker.load_snapshot(f) for f in snapshot_files]
    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trades = trade_logger.load_all()

    metrics = compute_metrics(snapshots, trades)

    click.echo(f"\n{'='*50}")
    click.echo("Performance Metrics")
    click.echo(f"{'='*50}")
    click.echo(f"Cumulative Return:   {metrics.cumulative_return_pct:>+10.2f}%")
    click.echo(f"Sharpe Ratio:        {metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 'N/A':>10}")
    click.echo(f"Max Drawdown:        {metrics.max_drawdown_pct:>10.2f}%")
    click.echo(f"Win Rate:            {metrics.win_rate_pct:>10.1f}%")
    click.echo(f"Avg Win:             {metrics.avg_win_pct:>+10.2f}%")
    click.echo(f"Avg Loss:            {metrics.avg_loss_pct:>+10.2f}%")
    click.echo(f"Top-3 Concentration: {metrics.concentration_top3_pct:>10.1f}%")
    click.echo(f"Total Trades:        {metrics.total_trades:>10}")
    click.echo(f"  Winning:           {metrics.winning_trades:>10}")
    click.echo(f"  Losing:            {metrics.losing_trades:>10}")


# ── LOG TRADE ─────────────────────────────────────────────────────────────────

@cli.command("log-trade")
@click.option("--ticker", "-t", required=True, help="Ticker symbol (e.g. AAPL).")
@click.option("--action", "-a", required=True, type=click.Choice(["BUY", "SELL"], case_sensitive=False), help="BUY or SELL.")
@click.option("--shares", "-s", required=True, type=int, help="Number of shares.")
@click.option("--price", "-p", required=True, type=float, help="Execution price per share.")
@click.option("--rationale", "-r", default="", help="Reason for the trade.")
@click.option("--tags", default="", help="Comma-separated tags (e.g. 'momentum,earnings').")
def log_trade(ticker, action, shares, price, rationale, tags):
    """Log a new trade with rationale."""
    settings, _ = _load_config()
    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))

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
            sys.exit(1)

    trade_logger = TradeLogger(settings.get("trade_history_file", "data/trade_history.json"))
    trade_history_obj = TradeHistory(trade_logger)
    recent_trades = trade_history_obj.get_recent(recent_n)

    # Try to compute metrics from all snapshots
    snapshot_files = tracker.list_snapshots()
    metrics = None
    if len(snapshot_files) >= 2:
        all_snapshots = [tracker.load_snapshot(f) for f in snapshot_files]
        metrics = compute_metrics(all_snapshots, trade_logger.load_all())

    user_question = question or click.prompt(
        "What would you like to ask the LLM?",
        default="Analyze my portfolio and recommend trades for this week.",
    )

    try:
        rendered = generate_prompt(
            prompt_type=prompt_type,
            snapshot=snap,
            recent_trades=recent_trades,
            metrics=metrics,
            persistent_ctx=persistent_ctx,
            user_question=user_question,
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
        auto_file = output_dir / f"prompt_{prompt_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        auto_file.write_text(rendered, encoding="utf-8")

        click.echo("\n" + "=" * 60)
        click.echo(rendered)
        click.echo("=" * 60)
        click.echo(f"\nPrompt length: ~{token_count} tokens")
        click.secho(f"Auto-saved to: {auto_file}", fg="green")


if __name__ == "__main__":
    cli()
