"""Tests for daily journal persistence."""

from datetime import datetime

import pytest

from src.data_ingestion.models import PerformanceMetrics, PortfolioSnapshot, Position, Trade
from src.trade_log.journal import JournalStore


def make_snapshot(ts: datetime | None = None) -> PortfolioSnapshot:
    timestamp = ts or datetime(2026, 4, 22, 18, 42)
    position = Position(
        ticker="AAPL",
        company_name="Apple Inc.",
        shares=2.0,
        avg_cost_basis=100.0,
        current_price=120.0,
        market_value=240.0,
        unrealized_pnl=40.0,
        unrealized_pnl_pct=20.0,
        weight_pct=80.0,
    )
    return PortfolioSnapshot(
        timestamp=timestamp,
        total_portfolio_value=300.0,
        cash=60.0,
        invested_value=240.0,
        today_change=5.0,
        today_change_pct=1.69,
        total_gain_loss=40.0,
        total_gain_loss_pct=20.0,
        cumulative_return_pct=15.0,
        positions=[position],
    )


def make_trade(ts: datetime | None = None) -> Trade:
    return Trade(
        id="trade-1",
        ticker="AAPL",
        action="BUY",
        shares=1.0,
        price=120.0,
        total_value=120.0,
        rationale="Add to winner",
        tags=["momentum"],
        timestamp=ts or datetime(2026, 4, 22, 19, 0),
    )


class TestJournalStore:
    def test_record_snapshot_creates_daily_entry(self, tmp_path):
        store = JournalStore(tmp_path / "journal.json")
        snapshot = make_snapshot()
        metrics = PerformanceMetrics(max_drawdown_pct=4.5, concentration_top3_pct=80.0)

        entry = store.record_snapshot(
            snapshot=snapshot,
            snapshot_path="data/portfolio_snapshots/snapshot_20260422_184200.json",
            trades=[],
            metrics=metrics,
        )

        assert entry.entry_date == "2026-04-22"
        assert entry.snapshot is not None
        assert entry.snapshot.positions_count == 1
        assert entry.snapshot.total_value == pytest.approx(300.0)
        assert entry.pnl_summary is not None
        assert entry.pnl_summary.unrealized_pnl == pytest.approx(40.0)
        assert entry.pnl_summary.max_drawdown_pct == pytest.approx(4.5)

    def test_add_trade_updates_existing_entry_without_duplicates(self, tmp_path):
        store = JournalStore(tmp_path / "journal.json")
        trade = make_trade()

        store.add_trade(trade)
        entry = store.add_trade(trade)

        assert len(entry.trades) == 1
        assert entry.trades[0].id == "trade-1"

    def test_add_prompt_and_decision_use_same_daily_entry(self, tmp_path):
        store = JournalStore(tmp_path / "journal.json")
        created_at = datetime(2026, 4, 22, 20, 0)

        store.add_prompt(
            prompt_type="trade",
            question="What should I do tomorrow?",
            output_path="output/prompts/tomorrow.txt",
            token_count=850,
            snapshot_path="data/portfolio_snapshots/snapshot_20260422_184200.json",
            created_at=created_at,
        )
        entry = store.add_decision(
            entry_date="2026-04-22",
            response_text="Hold AAPL and trim GLDM.",
            summary="Trim gold, otherwise hold.",
            prompt_output_path="output/prompts/tomorrow.txt",
            recorded_at=datetime(2026, 4, 22, 20, 30),
        )

        assert len(entry.prompts) == 1
        assert entry.prompts[0].question == "What should I do tomorrow?"
        assert len(entry.decisions) == 1
        assert entry.decisions[0].prompt_output_path == "output/prompts/tomorrow.txt"

    def test_record_snapshot_preserves_existing_prompts_and_trades(self, tmp_path):
        store = JournalStore(tmp_path / "journal.json")
        trade = make_trade()
        store.add_trade(trade)
        store.add_prompt(
            prompt_type="trade",
            question="What should I do tomorrow?",
            output_path="output/prompts/tomorrow.txt",
            token_count=850,
            created_at=datetime(2026, 4, 22, 20, 0),
        )

        entry = store.record_snapshot(
            snapshot=make_snapshot(),
            snapshot_path="data/portfolio_snapshots/snapshot_20260422_184200.json",
            trades=[trade],
        )

        assert len(entry.trades) == 1
        assert len(entry.prompts) == 1
        assert entry.snapshot is not None