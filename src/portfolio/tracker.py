"""
tracker.py - Core portfolio state engine.

Converts raw ingestion data (from paste parser or CSV) into enriched
PortfolioSnapshot objects by:
  1. Applying live market quotes via yfinance
  2. Computing per-position cost basis from trade history
  3. Calculating weights and P&L
  4. Saving snapshots to disk
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.data_ingestion.market_data import get_current_prices
from src.data_ingestion.models import (
    PortfolioSnapshot,
    Position,
    RawPortfolioData,
    RawPosition,
    Trade,
)


class PortfolioTracker:
    """
    Transforms raw portfolio data into enriched snapshots.

    Args:
        snapshots_dir: Directory where snapshots are saved as JSON files.
        refresh_prices: If True, fetch live prices from yfinance to override
                        prices from the raw data.
    """

    def __init__(
        self,
        snapshots_dir: str | Path = "data/portfolio_snapshots",
        refresh_prices: bool = False,
    ) -> None:
        self.snapshots_dir = Path(snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.refresh_prices = refresh_prices

    def build_snapshot(
        self,
        raw: RawPortfolioData,
        trade_history: Optional[List[Trade]] = None,
    ) -> PortfolioSnapshot:
        """
        Build a PortfolioSnapshot from raw portfolio data.

        Args:
            raw: Parsed portfolio data from paste parser or CSV loader.
            trade_history: Optional list of past trades used to compute
                           per-position average cost basis. If not provided,
                           cost_basis_per_share from the raw data is used.

        Returns:
            An enriched PortfolioSnapshot.
        """
        trade_history = trade_history or []

        # Optionally refresh prices from yfinance
        live_prices: Dict[str, Optional[float]] = {}
        if self.refresh_prices and raw.positions:
            tickers = [p.ticker for p in raw.positions]
            live_prices = get_current_prices(tickers)

        positions: List[Position] = []
        total_invested = 0.0

        for raw_pos in raw.positions:
            current_price = live_prices.get(raw_pos.ticker) or raw_pos.current_price

            # Use trade-history derived avg cost if available, else raw cost basis
            avg_cost = _compute_avg_cost(trade_history, raw_pos.ticker)
            if avg_cost == 0.0:
                avg_cost = raw_pos.cost_basis_per_share

            market_value = raw_pos.shares * current_price
            total_invested += market_value

            unrealized_pnl = (current_price - avg_cost) * raw_pos.shares
            unrealized_pnl_pct = (
                ((current_price / avg_cost) - 1) * 100 if avg_cost else 0.0
            )

            positions.append(
                Position(
                    ticker=raw_pos.ticker,
                    company_name=raw_pos.company_name,
                    shares=raw_pos.shares,
                    avg_cost_basis=avg_cost,
                    current_price=current_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_pct=unrealized_pnl_pct,
                    day_change=raw_pos.day_change,
                    day_change_pct=raw_pos.day_change_pct,
                )
            )

        total_value = raw.total_value if raw.total_value else total_invested + raw.cash

        # Compute position weights
        for pos in positions:
            pos.weight_pct = (pos.market_value / total_value * 100) if total_value else 0.0

        # Compute cumulative return
        starting_value = total_value - raw.total_gain_loss if raw.total_gain_loss else total_value
        cumulative_return_pct = (
            ((total_value / starting_value) - 1) * 100
            if starting_value and starting_value != 0
            else 0.0
        )

        snapshot = PortfolioSnapshot(
            total_portfolio_value=total_value,
            cash=raw.cash,
            invested_value=total_invested,
            today_change=raw.today_change,
            today_change_pct=raw.today_change_pct,
            total_gain_loss=raw.total_gain_loss,
            total_gain_loss_pct=raw.total_gain_loss_pct,
            cumulative_return_pct=cumulative_return_pct,
            positions=positions,
        )
        return snapshot

    def save_snapshot(self, snapshot: PortfolioSnapshot) -> Path:
        """
        Save a snapshot to disk as a JSON file.

        Args:
            snapshot: The PortfolioSnapshot to save.

        Returns:
            Path to the saved file.
        """
        filename = snapshot.timestamp.strftime("snapshot_%Y%m%d_%H%M%S.json")
        filepath = self.snapshots_dir / filename
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write(snapshot.model_dump_json(indent=2))
        return filepath

    def load_snapshot(self, filepath: str | Path) -> PortfolioSnapshot:
        """Load a previously saved snapshot from a JSON file."""
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return PortfolioSnapshot.model_validate(data)

    def load_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        """
        Load the most recently saved snapshot from the snapshots directory.

        Returns:
            The most recent PortfolioSnapshot, or None if none exist.
        """
        files = sorted(self.snapshots_dir.glob("snapshot_*.json"))
        if not files:
            return None
        return self.load_snapshot(files[-1])

    def list_snapshots(self) -> List[Path]:
        """Return all snapshot files sorted oldest-first."""
        return sorted(self.snapshots_dir.glob("snapshot_*.json"))


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _compute_avg_cost(trades: List[Trade], ticker: str) -> float:
    """
    Compute the average cost basis for a ticker from trade history using
    the FIFO-average cost method.

    Returns 0.0 if there are no BUY trades for this ticker.
    """
    total_shares = 0.0
    total_cost = 0.0

    for trade in sorted(trades, key=lambda t: t.timestamp):
        if trade.ticker != ticker:
            continue
        if trade.action == "BUY":
            total_cost += trade.shares * trade.price
            total_shares += trade.shares
        elif trade.action == "SELL":
            if total_shares > 0:
                avg = total_cost / total_shares
                total_cost -= avg * trade.shares
                total_shares -= trade.shares

    return (total_cost / total_shares) if total_shares > 0 else 0.0
