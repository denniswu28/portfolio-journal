"""
option_logger.py - Persist option trades and open option positions.

Mirrors ``TradeLogger`` but for options. Two JSON stores:
  * ``data/options_history.json``   - append-only OptionTrade events (OPEN/CLOSE/ROLL).
  * ``data/options_positions.json`` - the current set of OptionPosition records.

The monitor reads open positions from here; a human still places every order.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.options.models import OptionPosition, OptionStrategy, OptionTrade


class OptionTradeLogger:
    """Persist option trade events and maintain the open-positions store."""

    def __init__(
        self,
        history_file: str | Path = "data/options_history.json",
        positions_file: str | Path = "data/options_positions.json",
    ) -> None:
        self.history_file = Path(history_file)
        self.positions_file = Path(positions_file)
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.positions_file.parent.mkdir(parents=True, exist_ok=True)

    # ── open ─────────────────────────────────────────────────────────────────

    def log_open(
        self,
        strategy: OptionStrategy,
        net_debit: float,
        rationale: str = "",
        tags: Optional[List[str]] = None,
        take_profit_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        close_by_dte: Optional[int] = 21,
        timestamp: Optional[datetime] = None,
    ) -> OptionPosition:
        """Record an opening trade and add a new open position."""
        ts = timestamp or datetime.now()
        position = OptionPosition(
            strategy=strategy,
            entry_net_debit=net_debit,
            opened_at=ts,
            status="OPEN",
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            close_by_dte=close_by_dte,
            rationale=rationale,
            tags=tags or [],
        )
        contracts = max((leg.contracts for leg in strategy.legs), default=1)
        trade = OptionTrade(
            position_id=position.id,
            underlying=strategy.underlying,
            structure=strategy.name,
            action="OPEN",
            net_debit=net_debit,
            contracts=contracts,
            timestamp=ts,
            rationale=rationale,
            tags=tags or [],
        )
        self._append_history(trade)
        positions = self.load_positions()
        positions.append(position)
        self._save_positions(positions)
        return position

    # ── close ────────────────────────────────────────────────────────────────

    def log_close(
        self,
        position_id: str,
        net_debit: float,
        rationale: str = "",
        timestamp: Optional[datetime] = None,
    ) -> Optional[OptionPosition]:
        """Mark a position CLOSED and record the closing trade."""
        ts = timestamp or datetime.now()
        positions = self.load_positions()
        target: Optional[OptionPosition] = None
        for pos in positions:
            if pos.id == position_id and pos.status == "OPEN":
                pos.status = "CLOSED"
                pos.closed_at = ts
                pos.exit_net_debit = net_debit
                target = pos
                break
        if target is None:
            return None
        self._save_positions(positions)
        trade = OptionTrade(
            position_id=position_id,
            underlying=target.strategy.underlying,
            structure=target.strategy.name,
            action="CLOSE",
            net_debit=net_debit,
            contracts=max((leg.contracts for leg in target.strategy.legs), default=1),
            timestamp=ts,
            rationale=rationale,
        )
        self._append_history(trade)
        return target

    # ── load ─────────────────────────────────────────────────────────────────

    def load_positions(self) -> List[OptionPosition]:
        if not self.positions_file.exists():
            return []
        with open(self.positions_file, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return [OptionPosition.model_validate(p) for p in raw]

    def load_open_positions(self) -> List[OptionPosition]:
        return [p for p in self.load_positions() if p.status == "OPEN"]

    def load_history(self) -> List[OptionTrade]:
        if not self.history_file.exists():
            return []
        with open(self.history_file, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return [OptionTrade.model_validate(t) for t in raw]

    # ── persistence ──────────────────────────────────────────────────────────

    def _append_history(self, trade: OptionTrade) -> None:
        history = self.load_history()
        history.append(trade)
        with open(self.history_file, "w", encoding="utf-8") as fh:
            json.dump([t.model_dump(mode="json") for t in history], fh, indent=2, default=str)

    def _save_positions(self, positions: List[OptionPosition]) -> None:
        with open(self.positions_file, "w", encoding="utf-8") as fh:
            json.dump([p.model_dump(mode="json") for p in positions], fh, indent=2, default=str)
