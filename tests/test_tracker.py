"""Tests for portfolio snapshot persistence."""

from datetime import datetime

from src.portfolio.tracker import PortfolioTracker
from tests.test_analytics import make_snapshot


def test_save_snapshot_uses_dated_folder(tmp_path):
    tracker = PortfolioTracker(tmp_path)
    snapshot = make_snapshot(100000.0, ts=datetime(2026, 5, 6, 14, 5))

    path = tracker.save_snapshot(snapshot)

    assert path.parent.name == "2026-05-06"
    assert path.name == "snapshot_20260506_140500.json"


def test_list_snapshots_finds_legacy_and_dated_files(tmp_path):
    tracker = PortfolioTracker(tmp_path)
    legacy = make_snapshot(90000.0, ts=datetime(2026, 4, 22, 18, 42, 54))
    dated = make_snapshot(100000.0, ts=datetime(2026, 5, 6, 14, 5))

    legacy_path = tmp_path / "snapshot_20260422_184254.json"
    legacy_path.write_text(legacy.model_dump_json(), encoding="utf-8")
    dated_path = tmp_path / "2026-05-06" / "snapshot_20260506_140500.json"
    dated_path.parent.mkdir()
    dated_path.write_text(dated.model_dump_json(), encoding="utf-8")

    paths = tracker.list_snapshots()

    assert paths == [legacy_path, dated_path]
    assert tracker.load_latest_snapshot().timestamp == datetime(2026, 5, 6, 14, 5)