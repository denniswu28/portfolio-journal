"""CLI regression tests for the Fidelity-only workflow."""

import json
from pathlib import Path

from click.testing import CliRunner

import main as main_module
from main import cli
from src.data_ingestion.models import PersistentContext


FIDELITY_CSV = """Account Number,Account Name,Basket Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type
Z25225580,Individual - TOD,,SPAXX**,HELD IN MONEY MARKET,,,,$2069.43,,,,,100.00%,,,Cash,
Z26679636,Cash Management (Individual - TOD),Tech,AAPL,APPLE INC,2.543,$273.17,+$7.00,$694.67,+$17.80,+2.62%,+$23.62,+3.52%,4.96%,$671.05,$263.88,Cash,
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
    assert len(journal_data[0]["prompts"]) == 1
    assert journal_data[0]["prompts"][0]["question"] == "How should I act tomorrow?"


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