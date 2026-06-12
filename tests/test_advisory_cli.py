"""CLI smoke test for `daily-advisory` (offline via --no-network)."""

from datetime import datetime

from click.testing import CliRunner

import main as main_module
from main import cli
from src.data_ingestion.models import OptionsGating, PersistentContext, PortfolioSnapshot, Position
from src.portfolio.tracker import PortfolioTracker


def _settings(tmp_path):
    return {
        "snapshots_dir": str(tmp_path / "snaps"),
        "reports_dir": str(tmp_path / "reports"),
        "output_dir": str(tmp_path / "prompts"),
        "trade_history_file": str(tmp_path / "trades.json"),
        "journal_file": str(tmp_path / "journal.json"),
    }


def _save_snapshot(tmp_path):
    positions = [
        Position(ticker="AAPL", company_name="Apple", shares=10, avg_cost_basis=100,
                 current_price=130, market_value=2000, unrealized_pnl_pct=30,
                 weight_pct=20.0, basket_name="Tech"),  # over 10% cap -> ACTION
        Position(ticker="MSFT", company_name="Microsoft", shares=5, avg_cost_basis=100,
                 current_price=120, market_value=6000, unrealized_pnl_pct=20,
                 weight_pct=60.0, basket_name="Tech"),
        Position(ticker="FXAIX", company_name="Fidelity 500", shares=10, avg_cost_basis=100,
                 current_price=100, market_value=1000, unrealized_pnl_pct=0,
                 weight_pct=10.0, basket_name=None),
    ]
    snap = PortfolioSnapshot(total_portfolio_value=10000, cash=1000, invested_value=9000,
                             positions=positions, timestamp=datetime(2026, 6, 7, 16, 0))
    tracker = PortfolioTracker(snapshots_dir=str(tmp_path / "snaps"))
    tracker.save_snapshot(snap)


def test_daily_advisory_gated_offline(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _save_snapshot(tmp_path)
    (tmp_path / "boist-2026-06-07.md").write_text(
        "# Memory thesis\nAAPL and MSFT strong.\n## Plan\nHold.\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))

    result = CliRunner().invoke(cli, [
        "daily-advisory", "--no-network", "--no-prompt",
        "--data-dir", str(tmp_path),
        "--event-horizon-days", "30",
        "--output-dir", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 0, result.output
    assert "ADVISORY ONLY (gated)" in result.output

    md = list((tmp_path / "reports").rglob("daily_advisory_2026-06-07.md"))
    js = list((tmp_path / "reports").rglob("daily_advisory_2026-06-07.json"))
    assert md and js
    text = md[0].read_text(encoding="utf-8")
    assert "AAPL exceeds 10% cap" in text
    assert "ADVISORY ONLY - NOT EXECUTABLE" in text
    assert "Memory thesis" in text


def test_daily_advisory_executable_when_enabled(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _save_snapshot(tmp_path)
    ctx = PersistentContext(options_gating=OptionsGating(options_enabled=True,
                                                         options_min_account_value=5000))
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, ctx))

    result = CliRunner().invoke(cli, [
        "daily-advisory", "--no-network", "--no-prompt",
        "--data-dir", str(tmp_path), "--output-dir", str(tmp_path / "reports"),
    ])
    assert result.exit_code == 0, result.output
    assert "OPTIONS GATE: EXECUTABLE" in result.output


def test_daily_advisory_requires_snapshot(monkeypatch, tmp_path):
    settings = _settings(tmp_path)  # no snapshot saved
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    result = CliRunner().invoke(cli, ["daily-advisory", "--no-network", "--no-prompt"])
    assert result.exit_code != 0
    assert "No snapshot available" in result.output
