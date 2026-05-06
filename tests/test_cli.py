"""CLI regression tests for the Fidelity-only workflow."""

import json
from pathlib import Path

import pandas as pd
from click.testing import CliRunner

import main as main_module
from main import cli
from src.data_ingestion.models import PersistentContext


FIDELITY_CSV = """Account Number,Account Name,Basket Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type
Z25225580,Individual - TOD,,SPAXX**,HELD IN MONEY MARKET,,,,$2069.43,,,,,100.00%,,,Cash,
Z26679636,Cash Management (Individual - TOD),Tech,AAPL,APPLE INC,2.543,$273.17,+$7.00,$694.67,+$17.80,+2.62%,+$23.62,+3.52%,4.96%,$671.05,$263.88,Cash,

"Date downloaded Apr-22-2026 9:29 p.m ET"
"""


def _make_settings(tmp_path):
    return {
        "snapshots_dir": str(tmp_path / "portfolio_snapshots"),
        "trade_history_file": str(tmp_path / "trade_history.json"),
        "journal_file": str(tmp_path / "journal.json"),
        "output_dir": str(tmp_path / "output" / "prompts"),
    }


def _write_csv(tmp_path):
    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(FIDELITY_CSV, encoding="utf-8")
    return csv_path


def test_sync_help_hides_legacy_modes():
    runner = CliRunner()

    result = runner.invoke(cli, ["sync", "--help"])

    assert result.exit_code == 0
    assert "--paste" not in result.output
    assert "--file" not in result.output
    assert "Fidelity CSV" in result.output


def test_sync_requires_input_file():
    runner = CliRunner()

    result = runner.invoke(cli, ["sync"])

    assert result.exit_code != 0
    assert "--input-file" in result.output


def test_sync_creates_journal_entry(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    csv_path = _write_csv(tmp_path)

    monkeypatch.setattr(
        main_module,
        "_load_config",
        lambda: (settings, PersistentContext()),
    )

    result = runner.invoke(cli, ["sync", "-i", str(csv_path)])

    assert result.exit_code == 0
    journal_path = Path(settings["journal_file"])
    journal_data = json.loads(journal_path.read_text(encoding="utf-8"))
    assert len(journal_data) == 1
    assert journal_data[0]["snapshot"]["positions_count"] == 1
    assert journal_data[0]["snapshot"]["snapshot_timestamp"].startswith("2026-04-22T21:29:00")
    assert "snapshot_20260422_212900.json" in journal_data[0]["snapshot"]["snapshot_path"]
    assert journal_data[0]["pnl_summary"]["unrealized_pnl"] != 0


def test_prompt_auto_logs_journal_entry(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    csv_path = _write_csv(tmp_path)

    monkeypatch.setattr(
        main_module,
        "_load_config",
        lambda: (settings, PersistentContext()),
    )

    sync_result = runner.invoke(cli, ["sync", "-i", str(csv_path)])
    assert sync_result.exit_code == 0

    prompt_path = tmp_path / "tomorrow_prompt.txt"
    prompt_result = runner.invoke(
        cli,
        [
            "prompt",
            "--type",
            "trade",
            "-q",
            "How should I act tomorrow?",
            "-o",
            str(prompt_path),
        ],
    )

    assert prompt_result.exit_code == 0
    journal_data = json.loads(Path(settings["journal_file"]).read_text(encoding="utf-8"))
    prompt_entries = [entry for entry in journal_data if entry["prompts"]]
    assert len(prompt_entries) == 1
    assert prompt_entries[0]["prompts"][0]["question"] == "How should I act tomorrow?"


def test_record_decision_writes_to_journal(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)

    monkeypatch.setattr(
        main_module,
        "_load_config",
        lambda: (settings, PersistentContext()),
    )

    result = runner.invoke(
        cli,
        [
            "record-decision",
            "--date",
            "2026-04-22",
            "--prompt-file",
            "output/prompts/tomorrow.txt",
            "--summary",
            "Trim gold and hold tech.",
            "--response-text",
            "Trim GLDM, otherwise hold existing tech names.",
        ],
    )

    assert result.exit_code == 0
    journal_data = json.loads(Path(settings["journal_file"]).read_text(encoding="utf-8"))
    assert len(journal_data) == 1
    assert journal_data[0]["decisions"][0]["summary"] == "Trim gold and hold tech."


def test_report_creates_plots_and_metric_spreadsheets(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    csv_path = _write_csv(tmp_path)
    report_dir = tmp_path / "reports"

    monkeypatch.setattr(
        main_module,
        "_load_config",
        lambda: (settings, PersistentContext()),
    )

    sync_result = runner.invoke(cli, ["sync", "-i", str(csv_path)])
    assert sync_result.exit_code == 0

    result = runner.invoke(cli, ["report", "-o", str(report_dir)])

    assert result.exit_code == 0
    assert (report_dir / "metrics_summary.csv").exists()
    assert (report_dir / "portfolio_timeseries.csv").exists()
    assert (report_dir / "return_series.csv").exists()
    assert (report_dir / "return_drawdown.png").exists()
    assert (report_dir / "unrealized_pnl.png").exists()
    assert (report_dir / "position_weights.png").exists()
    metrics_text = (report_dir / "metrics_summary.csv").read_text(encoding="utf-8")
    assert "sharpe_ratio" in metrics_text
    assert "calmar_ratio" in metrics_text
    assert "max_drawdown_pct" in metrics_text


def test_organize_exports_and_sync_bundle(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    positions_path = raw_dir / "Portfolio_Positions_Apr-22-2026.csv"
    positions_path.write_text(FIDELITY_CSV, encoding="utf-8")
    allocation_path = raw_dir / "Asset_allocation.csv"
    allocation_path.write_text(
        '"Symbol","Description","Account","Asset class","Weight","Current value"\n'
        '"AAPL","Apple Inc","Z26679636","Domestic Stock","100.00%","$694.67"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        main_module,
        "_load_config",
        lambda: (settings, PersistentContext()),
    )

    organize_result = runner.invoke(
        cli,
        [
            "organize-exports",
            "--date",
            "2026-04-22",
            str(positions_path),
            str(allocation_path),
        ],
    )
    assert organize_result.exit_code == 0
    bundle_dir = Path(settings["snapshots_dir"]) / "2026-04-22"
    assert (bundle_dir / "positions.csv").exists()
    assert (bundle_dir / "asset_allocation.csv").exists()
    assert (bundle_dir / "manifest.json").exists()

    sync_result = runner.invoke(cli, ["sync-bundle", "--date", "2026-04-22"])

    assert sync_result.exit_code == 0
    assert "Supplemental Fidelity analysis attached" in sync_result.output
    snapshot_files = list(bundle_dir.glob("snapshot_20260422_212900.json"))
    assert len(snapshot_files) == 1
    journal_data = json.loads(Path(settings["journal_file"]).read_text(encoding="utf-8"))
    assert journal_data[0]["exposure_summary"]["top_asset_classes"] == [
        "Domestic Stock: 100.0%"
    ]


def test_rebalance_weights_writes_reports(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    report_dir = tmp_path / "reports"
    universe_path = tmp_path / "growth_universe.yaml"
    universe_path.write_text(
        "settings:\n"
        "  cash_target_pct: 10\n"
        "sleeves:\n"
        "  - name: Stable growth\n"
        "    proxy: LOW\n"
        "    max_weight_pct: 80\n"
        "  - name: Speculative growth\n"
        "    proxy: HIGH\n"
        "    max_weight_pct: 80\n",
        encoding="utf-8",
    )
    dates = pd.date_range("2024-01-05", periods=81, freq="W-FRI")
    price_history = pd.DataFrame(
        {
            "LOW": [100 + value for value in range(81)],
            "HIGH": [100 + ((-1) ** value) * value for value in range(81)],
        },
        index=dates,
    )

    monkeypatch.setattr(
        main_module,
        "_load_config",
        lambda: (settings, PersistentContext()),
    )
    monkeypatch.setattr(
        main_module,
        "get_price_history",
        lambda tickers, period, interval: price_history,
    )

    result = runner.invoke(
        cli,
        [
            "rebalance-weights",
            "--universe",
            str(universe_path),
            "--method",
            "equal-vol",
            "--min-observations",
            "20",
            "--output-dir",
            str(report_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Portfolio Theory Rebalance" in result.output
    assert list(report_dir.glob("rebalance_weights_*.csv"))
    assert list(report_dir.glob("rebalance_plan_*.md"))