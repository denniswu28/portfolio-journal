"""CLI regression tests for the Fidelity-only workflow."""

import json
from datetime import date, timedelta
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


def test_basket_plan_shows_decomposition_and_resizes(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    csv_path = _write_csv(tmp_path)

    sync_result = runner.invoke(cli, ["sync", "--input-file", str(csv_path)])
    assert sync_result.exit_code == 0

    # No --basket: show the decomposition (offline, no new tickers).
    show = runner.invoke(cli, ["basket-plan"])
    assert show.exit_code == 0
    assert "Tech" in show.output

    # Method B resize on an existing basket needs no network.
    report_dir = tmp_path / "reports"
    resize = runner.invoke(
        cli,
        ["basket-plan", "--basket", "Tech", "--resize-by", "-100", "--output-dir", str(report_dir)],
    )
    assert resize.exit_code == 0
    assert "Method B - resize" in resize.output
    assert list(report_dir.rglob("basket_plan_*.md"))


def test_options_analyze_writes_ticket(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    report_dir = tmp_path / "reports"

    # Fully specified spot + vol + expiry -> no network access required.
    result = runner.invoke(
        cli,
        [
            "options-analyze", "--underlying", "SMH", "--structure", "bull-put-spread",
            "--strikes", "580,530", "--expiry", "2026-07-17", "--vol", "0.30",
            "--rate", "0.043", "--spot", "632", "--output-dir", str(report_dir),
        ],
    )
    assert result.exit_code == 0
    assert "bull put spread" in result.output
    assert "Max profit" in result.output
    assert list(report_dir.rglob("option_ticket_*.md"))
    assert list(report_dir.rglob("option_payoff_*.png"))


def test_log_option_then_monitor(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = {
        **_make_settings(tmp_path),
        "options_history_file": str(tmp_path / "options_history.json"),
        "options_positions_file": str(tmp_path / "options_positions.json"),
    }
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))

    expiry = (date.today() + timedelta(days=40)).isoformat()
    logged = runner.invoke(
        cli,
        [
            "log-option", "-u", "SMH", "--structure", "bull-put-spread",
            "--strikes", "580,530", "--expiry", expiry, "--net-debit", "-553",
            "--rationale", "boist put-sell", "--tags", "boist",
        ],
    )
    assert logged.exit_code == 0, logged.output
    assert Path(settings["options_positions_file"]).exists()

    # Monitor offline: mock the market-data calls.
    monkeypatch.setattr(main_module, "get_current_prices", lambda tickers: {t: 600.0 for t in tickers})
    monkeypatch.setattr(main_module, "realized_volatility", lambda u, w: 0.30)
    report_dir = tmp_path / "monitor_reports"
    monitored = runner.invoke(cli, ["monitor", "--rate", "0.04", "--output-dir", str(report_dir)])
    assert monitored.exit_code == 0, monitored.output
    assert list(report_dir.rglob("monitor_*.md"))


def test_options_analyze_rejects_naked_call(monkeypatch, tmp_path):
    runner = CliRunner()
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))

    result = runner.invoke(
        cli,
        [
            "options-analyze", "--underlying", "SMH", "--leg", "SELL CALL 700",
            "--expiry", "2026-07-17", "--vol", "0.30", "--rate", "0.043", "--spot", "632",
            "--output-dir", str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0
    assert "Level-2 violations" in result.output or "REJECTED" in result.output


def test_catalyst_prompt_with_snapshot(monkeypatch, tmp_path):
    """Success path: sync a snapshot first, then run catalyst-prompt and assert file written."""
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

    result = runner.invoke(cli, [
        "catalyst-prompt", "--date", "2026-06-12",
        "--output-dir", str(tmp_path / "prompts"),
        "--held-only",
    ])
    assert result.exit_code == 0
    written = list((tmp_path / "prompts").glob("catalyst_research_*.txt"))
    assert written, "expected a catalyst_research_*.txt file"
    body = written[0].read_text(encoding="utf-8")
    assert "items:" in body and "direction:" in body


def test_catalyst_ingest_round_trip(tmp_path):
    runner = CliRunner()
    paste = tmp_path / "pasted.txt"
    paste.write_text(
        "as_of: 2026-06-12\n"
        "generated_by: perplexity\n"
        "items:\n"
        "  - {ticker: NVDA, direction: bull, summary: order}\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli, [
        "catalyst-ingest", "--date", "2026-06-12",
        "--file", str(paste), "--data-dir", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    out = tmp_path / "catalysts" / "catalyst-2026-06-12.yaml"
    assert out.exists()
    assert "NVDA" in out.read_text(encoding="utf-8")


def test_catalyst_ingest_bad_paste_exits_nonzero(tmp_path):
    runner = CliRunner()
    paste = tmp_path / "bad.txt"
    paste.write_text("items:\n  - {direction: bull, summary: no ticker}\n", encoding="utf-8")
    result = runner.invoke(cli, [
        "catalyst-ingest", "--date", "2026-06-12",
        "--file", str(paste), "--data-dir", str(tmp_path),
    ])
    assert result.exit_code != 0