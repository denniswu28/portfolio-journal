"""Daily journal persistence for snapshots, trades, prompts, and decisions."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from src.data_ingestion.models import (
    JournalDecisionRecord,
    JournalEntry,
    JournalPnlSummary,
    JournalPromptRecord,
    JournalSnapshotSummary,
    PerformanceMetrics,
    PortfolioSnapshot,
    Trade,
)
from src.portfolio.analytics import realized_pnl, total_pnl


class JournalStore:
    """Persist and update one journal entry per day."""

    def __init__(self, filepath: str | Path = "data/journal.json") -> None:
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> List[JournalEntry]:
        if not self.filepath.exists():
            return []
        with open(self.filepath, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        entries = [JournalEntry.model_validate(item) for item in raw]
        return sorted(entries, key=lambda entry: entry.entry_date)

    def get_entry(self, entry_date: str | date | datetime) -> Optional[JournalEntry]:
        key = _normalize_entry_date(entry_date)
        for entry in self.load_all():
            if entry.entry_date == key:
                return entry
        return None

    def record_snapshot(
        self,
        snapshot: PortfolioSnapshot,
        snapshot_path: str | Path,
        trades: Optional[List[Trade]] = None,
        metrics: Optional[PerformanceMetrics] = None,
    ) -> JournalEntry:
        entry = self._get_or_create_entry(snapshot.timestamp)
        unrealized = total_pnl(snapshot)

        entry.snapshot = JournalSnapshotSummary(
            snapshot_path=str(snapshot_path),
            snapshot_timestamp=snapshot.timestamp,
            total_value=snapshot.total_portfolio_value,
            cash=snapshot.cash,
            invested_value=snapshot.invested_value,
            today_change=snapshot.today_change,
            today_change_pct=snapshot.today_change_pct,
            total_gain_loss=snapshot.total_gain_loss,
            total_gain_loss_pct=snapshot.total_gain_loss_pct,
            cumulative_return_pct=snapshot.cumulative_return_pct,
            positions_count=len(snapshot.positions),
        )
        entry.pnl_summary = JournalPnlSummary(
            realized_pnl=realized_pnl(trades or []),
            unrealized_pnl=unrealized["dollars"],
            unrealized_pnl_pct=unrealized["percent"],
            total_gain_loss=snapshot.total_gain_loss,
            total_gain_loss_pct=snapshot.total_gain_loss_pct,
            cumulative_return_pct=snapshot.cumulative_return_pct,
            today_change=snapshot.today_change,
            today_change_pct=snapshot.today_change_pct,
            sharpe_ratio=metrics.sharpe_ratio if metrics else None,
            max_drawdown_pct=metrics.max_drawdown_pct if metrics else 0.0,
            win_rate_pct=metrics.win_rate_pct if metrics else 0.0,
            avg_win_pct=metrics.avg_win_pct if metrics else 0.0,
            avg_loss_pct=metrics.avg_loss_pct if metrics else 0.0,
            concentration_top3_pct=metrics.concentration_top3_pct if metrics else 0.0,
        )

        return self._upsert_entry(entry)

    def add_trade(self, trade: Trade) -> JournalEntry:
        entry = self._get_or_create_entry(trade.timestamp)
        existing_index = next(
            (index for index, existing_trade in enumerate(entry.trades) if existing_trade.id == trade.id),
            None,
        )

        if existing_index is None:
            entry.trades.append(trade)
        else:
            entry.trades[existing_index] = trade

        entry.trades.sort(key=lambda item: item.timestamp)
        return self._upsert_entry(entry)

    def add_prompt(
        self,
        prompt_type: str,
        question: str,
        output_path: str | Path,
        token_count: int,
        snapshot_path: str | Path = "",
        created_at: Optional[datetime] = None,
    ) -> JournalEntry:
        timestamp = created_at or datetime.now()
        entry = self._get_or_create_entry(timestamp)
        entry.prompts.append(
            JournalPromptRecord(
                created_at=timestamp,
                prompt_type=prompt_type,
                question=question,
                output_path=str(output_path),
                snapshot_path=str(snapshot_path),
                token_count=token_count,
            )
        )
        entry.prompts.sort(key=lambda item: item.created_at)
        return self._upsert_entry(entry)

    def add_decision(
        self,
        entry_date: str | date | datetime,
        response_text: str,
        summary: str = "",
        prompt_output_path: str | Path = "",
        recorded_at: Optional[datetime] = None,
    ) -> JournalEntry:
        entry = self._get_or_create_entry(entry_date)
        entry.decisions.append(
            JournalDecisionRecord(
                recorded_at=recorded_at or datetime.now(),
                prompt_output_path=str(prompt_output_path),
                summary=summary,
                response_text=response_text,
            )
        )
        entry.decisions.sort(key=lambda item: item.recorded_at)
        return self._upsert_entry(entry)

    def _get_or_create_entry(self, entry_date: str | date | datetime) -> JournalEntry:
        key = _normalize_entry_date(entry_date)
        entry = self.get_entry(key)
        if entry:
            return entry
        now = datetime.now()
        return JournalEntry(entry_date=key, created_at=now, updated_at=now)

    def _upsert_entry(self, updated_entry: JournalEntry) -> JournalEntry:
        entries = self.load_all()
        updated_entry.updated_at = datetime.now()

        for index, entry in enumerate(entries):
            if entry.entry_date == updated_entry.entry_date:
                entries[index] = updated_entry
                self._save_all(entries)
                return updated_entry

        entries.append(updated_entry)
        entries.sort(key=lambda entry: entry.entry_date)
        self._save_all(entries)
        return updated_entry

    def _save_all(self, entries: List[JournalEntry]) -> None:
        data = [entry.model_dump(mode="json") for entry in entries]
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)


def _normalize_entry_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)