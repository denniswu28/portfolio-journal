"""
logger.py - Record and persist trades with rationale.

Trades are stored in a JSON file (data/trade_history.json).
Each trade includes: ticker, action, shares, price, rationale, timestamp, tags.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.data_ingestion.models import Trade


class TradeLogger:
    """
    Persists trades to a JSON file and provides basic retrieval.

    Args:
        filepath: Path to the JSON file where trades are stored.
    """

    def __init__(self, filepath: str | Path = "data/trade_history.json") -> None:
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    def log_trade(
        self,
        ticker: str,
        action: str,
        shares: int,
        price: float,
        rationale: str = "",
        tags: Optional[List[str]] = None,
        timestamp: Optional[datetime] = None,
    ) -> Trade:
        """
        Log a new trade to the JSON file.

        Args:
            ticker: Stock ticker symbol.
            action: "BUY" or "SELL".
            shares: Number of shares.
            price: Execution price per share.
            rationale: Free-text explanation for the trade.
            tags: Optional list of tags (e.g., ["momentum", "earnings"]).
            timestamp: Trade datetime. Defaults to now.

        Returns:
            The created Trade object.
        """
        trade = Trade(
            id=str(uuid.uuid4()),
            ticker=ticker.upper(),
            action=action.upper(),
            shares=shares,
            price=price,
            total_value=shares * price,
            rationale=rationale,
            tags=tags or [],
            timestamp=timestamp or datetime.now(),
        )

        existing = self._load_all()
        existing.append(trade)
        self._save_all(existing)
        return trade

    def load_all(self) -> List[Trade]:
        """Load all trades from the JSON file."""
        return self._load_all()

    def _load_all(self) -> List[Trade]:
        if not self.filepath.exists():
            return []
        with open(self.filepath, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return [Trade.model_validate(t) for t in raw]

    def _save_all(self, trades: List[Trade]) -> None:
        data = [t.model_dump(mode="json") for t in trades]
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
