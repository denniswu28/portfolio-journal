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
from datetime import datetime
from pathlib import Path

import click
from tabulate import tabulate

# ── PATH SETUP ────────────────────────────────────────────────────────────────
# Allow running `python main.py` from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from src.data_ingestion.csv_loader import CSVLoader
from src.data_ingestion.models import PersistentContext
from src.portfolio.analytics import compute_metrics
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
    if not snapshots:
        return None
    return compute_metrics(snapshots, trades)


def _get_latest_snapshot_path(tracker):
    """Return the latest saved snapshot path, if any."""
    snapshot_files = tracker.list_snapshots()
    if not snapshot_files:
        return None
    return snapshot_files[-1]


@click.group()
def cli():
    """Portfolio Tracker — sync holdings, analyse them, and generate LLM prompts."""


# ── SYNC ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--input-file", "-i", required=True, help="Path to the Fidelity positions CSV file.")
@click.option("--cash", default=None, type=float, help="Cash balance override if you need to supplement the Fidelity CSV.")
@click.option("--refresh-prices", is_flag=True, default=False, help="Fetch live prices from yfinance.")
@click.option("--save/--no-save", default=True, help="Save snapshot to disk.")
def sync(input_file, cash, refresh_prices, save):
    """Sync portfolio from a Fidelity CSV and save a snapshot."""
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
    )

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
    prompt_created_at = datetime.now()

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
