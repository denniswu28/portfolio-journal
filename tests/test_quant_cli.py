"""CLI smoke tests for the quant commands (network mocked)."""

import numpy as np
import pandas as pd
from click.testing import CliRunner

import main as main_module
import src.quant.options_backtest as obt_module
import src.quant.signals as signals_module
from main import cli
from src.data_ingestion.models import PersistentContext


def _settings(tmp_path):
    return {
        "snapshots_dir": str(tmp_path / "snaps"),
        "journal_file": str(tmp_path / "journal.json"),
        "reports_dir": str(tmp_path / "reports"),
    }


def _weekly_panel(n=330, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-04", periods=n, freq="W-FRI")
    cols = {}
    for i, name in enumerate(["AAA", "BBB", "SPY", "SMH", "GRID", "GLDM", "IEF", "VXUS"]):
        rets = rng.normal(0.001 + i * 0.0001, 0.02, n)
        cols[name] = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(cols, index=idx)


def _daily_panel(n=320, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    cols = {}
    for name in ["AAA", "BBB", "SPY"]:
        cols[name] = 100 + np.cumsum(rng.normal(0, 0.3, n)).clip(-8, 8)
    return pd.DataFrame(cols, index=idx)


def _small_universe(tmp_path):
    path = tmp_path / "universe.yaml"
    path.write_text(
        "settings:\n  cash_target_pct: 10\n"
        "sleeves:\n"
        "  - name: Alpha\n    proxy: AAA\n    max_weight_pct: 80\n"
        "  - name: Beta\n    proxy: BBB\n    max_weight_pct: 80\n",
        encoding="utf-8",
    )
    return path


def test_backtest_command(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    monkeypatch.setattr(main_module, "get_price_history", lambda tickers, period, interval: _weekly_panel())
    result = CliRunner().invoke(cli, [
        "backtest", "--strategy", "momentum", "--tickers", "AAA,BBB",
        "--lookback", "13", "--top-k", "1", "--rebalance", "M", "--benchmark", "SPY",
    ])
    assert result.exit_code == 0, result.output
    assert "Sharpe" in result.output
    assert list((tmp_path / "reports").rglob("backtest_momentum_*.md"))


def test_backtest_walk_forward_command(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    monkeypatch.setattr(main_module, "get_price_history", lambda tickers, period, interval: _weekly_panel())
    result = CliRunner().invoke(cli, [
        "backtest", "--strategy", "momentum", "--tickers", "AAA,BBB",
        "--walk-forward", "--train", "104", "--test", "26", "--lookback", "13", "--top-k", "1",
    ])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "reports").rglob("backtest_wf_momentum_*.md"))


def test_optimize_params_command(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    monkeypatch.setattr(main_module, "get_price_history", lambda tickers, period, interval: _weekly_panel())
    result = CliRunner().invoke(cli, [
        "optimize-params", "--tickers", "AAA,BBB", "--lookbacks", "13,26",
        "--top-ks", "1", "--train", "104", "--test", "26", "--scorer", "sharpe",
    ])
    assert result.exit_code == 0, result.output
    assert "OOS" in result.output
    assert list((tmp_path / "reports").rglob("optimize_params_*.md"))


def test_factor_report_command(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    universe = _small_universe(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    monkeypatch.setattr(main_module, "get_price_history", lambda tickers, period, interval: _weekly_panel())
    result = CliRunner().invoke(cli, ["factor-report", "--universe", str(universe)])
    assert result.exit_code == 0, result.output
    assert "Systematic variance" in result.output
    assert list((tmp_path / "reports").rglob("factor_report_*.md"))


def test_signals_command(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    monkeypatch.setattr(signals_module, "get_price_history",
                        lambda tickers, period, interval: _daily_panel())
    result = CliRunner().invoke(cli, ["signals", "--tickers", "AAA,BBB", "--benchmark", "SPY"])
    assert result.exit_code == 0, result.output
    assert "Flags" in result.output
    assert list((tmp_path / "reports").rglob("signals_*.md"))


def test_options_backtest_command(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main_module, "_load_config", lambda: (settings, PersistentContext()))
    monkeypatch.setattr(obt_module, "get_price_history",
                        lambda tickers, period, interval: _daily_panel())
    monkeypatch.setattr(obt_module, "get_risk_free_rate", lambda *a, **k: 0.04)
    result = CliRunner().invoke(cli, [
        "options-backtest", "--ticker", "AAA", "--structure", "cash-secured-put",
        "--dte", "30", "--otm", "0.05",
    ])
    assert result.exit_code == 0, result.output
    assert "Theoretical" in result.output
    assert list((tmp_path / "reports").rglob("options_backtest_AAA_*.md"))
