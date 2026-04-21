"""
history.py - Query and filter trade history.

Provides filtering utilities on top of TradeLogger.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from src.data_ingestion.models import Trade
from src.trade_log.logger import TradeLogger


class TradeHistory:
    """
    Query interface for trade history.

    Args:
        logger: A TradeLogger instance to read trades from.
    """

    def __init__(self, logger: TradeLogger) -> None:
        self.logger = logger

    def get_all(self) -> List[Trade]:
        """Return all trades sorted oldest-first."""
        return sorted(self.logger.load_all(), key=lambda t: t.timestamp)

    def get_recent(self, n: int = 10) -> List[Trade]:
        """Return the N most recent trades."""
        return self.get_all()[-n:]

    def get_by_ticker(self, ticker: str) -> List[Trade]:
        """Return all trades for a specific ticker."""
        ticker = ticker.upper()
        return [t for t in self.get_all() if t.ticker == ticker]

    def get_by_action(self, action: str) -> List[Trade]:
        """Return trades filtered by action ('BUY' or 'SELL')."""
        action = action.upper()
        return [t for t in self.get_all() if t.action == action]

    def get_by_date_range(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Trade]:
        """Return trades within an optional date range."""
        trades = self.get_all()
        if start:
            trades = [t for t in trades if t.timestamp >= start]
        if end:
            trades = [t for t in trades if t.timestamp <= end]
        return trades

    def get_by_tag(self, tag: str) -> List[Trade]:
        """Return trades that include a specific tag."""
        tag = tag.lower()
        return [t for t in self.get_all() if tag in [tg.lower() for tg in t.tags]]

    def summary(self) -> dict:
        """Return a high-level summary of all trades."""
        trades = self.get_all()
        buy_trades = [t for t in trades if t.action == "BUY"]
        sell_trades = [t for t in trades if t.action == "SELL"]
        tickers = sorted({t.ticker for t in trades})
        return {
            "total_trades": len(trades),
            "buy_trades": len(buy_trades),
            "sell_trades": len(sell_trades),
            "unique_tickers": tickers,
            "first_trade": trades[0].timestamp.isoformat() if trades else None,
            "last_trade": trades[-1].timestamp.isoformat() if trades else None,
        }
