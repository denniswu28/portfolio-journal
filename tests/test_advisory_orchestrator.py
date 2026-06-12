"""Offline tests for the deterministic advisory orchestrator core."""

from datetime import date, datetime

from src.advisory.orchestrator import build_advisory_run, known_tickers
from src.data_ingestion.models import OptionsGating, PersistentContext, PortfolioSnapshot, Position
from src.portfolio.optimizer import SleeveDefinition
from src.quant.signals import SignalSet


def _snapshot():
    positions = [
        Position(ticker="MU", company_name="Micron", shares=2, avg_cost_basis=100,
                 current_price=120, market_value=5000, unrealized_pnl_pct=20,
                 weight_pct=33.3, basket_name="Memory"),
        Position(ticker="SNDK", company_name="SanDisk", shares=2, avg_cost_basis=100,
                 current_price=110, market_value=4000, unrealized_pnl_pct=-25,
                 weight_pct=26.7, basket_name="Memory"),
        Position(ticker="FXAIX", company_name="Fidelity 500", shares=2, avg_cost_basis=100,
                 current_price=100, market_value=5000, unrealized_pnl_pct=0,
                 weight_pct=33.3, basket_name=None),
    ]
    return PortfolioSnapshot(total_portfolio_value=15000, cash=1000, invested_value=14000,
                             positions=positions, timestamp=datetime(2026, 6, 6, 16, 0))


def _sleeves():
    return [SleeveDefinition(name="Memory", proxy="MU", holdings=("MU", "SNDK"),
                             min_weight_pct=40.0, max_weight_pct=55.0)]


def test_known_tickers_union():
    known = known_tickers(_sleeves(), _snapshot())
    assert {"MU", "SNDK", "FXAIX"}.issubset(known)


def test_build_run_gated_by_default(tmp_path):
    (tmp_path / "boist-2026-06-07.md").write_text(
        "# Memory thesis\nMU and SNDK shortage.\n## Plan\nHold memory.\n", encoding="utf-8")
    run = build_advisory_run(
        as_of=date(2026, 6, 7), snapshot=_snapshot(), snapshot_path="snap.json",
        ctx=PersistentContext(), sleeves=_sleeves(), generated_at="2026-06-07T09:00",
        data_dir=str(tmp_path), event_calendar_path=str(tmp_path / "none.yaml"),
    )
    # Default gating -> not executable.
    assert run.gate["executable"] is False
    assert run.options.gated is True
    # Rules fired: SNDK stop-loss (-25%) and Memory basket BELOW its 40-55% band.
    cats = {a.category for a in run.rule_alerts}
    assert "stop_loss" in cats and "band" in cats and "out_of_basket" in cats
    # Thesis found and tickers filtered to known.
    assert run.thesis.found and "MU" in run.thesis.tickers
    # Basket verdict present.
    assert any(c.basket == "Memory" for c in run.basket_actions)


def test_signals_enrich_basket_verdicts(tmp_path):
    signals = {"MU": SignalSet(ticker="MU", as_of=None, close=120.0,
                               composite={"overall": 0.8}, flags=["above_200dma"])}
    run = build_advisory_run(
        as_of=date(2026, 6, 7), snapshot=_snapshot(), snapshot_path="snap.json",
        ctx=PersistentContext(), sleeves=_sleeves(), generated_at="2026-06-07T09:00",
        data_dir=str(tmp_path), event_calendar_path=str(tmp_path / "none.yaml"),
        signals_by_ticker=signals,
    )
    memory = next(c for c in run.basket_actions if c.basket == "Memory")
    assert memory.signal_score == 0.8
    assert memory.confidence != ""
    assert memory.signal_ticker == "MU"


def test_build_run_executable_when_funded_and_enabled(tmp_path):
    ctx = PersistentContext(options_gating=OptionsGating(options_enabled=True,
                                                         options_min_account_value=10000))
    run = build_advisory_run(
        as_of=date(2026, 6, 7), snapshot=_snapshot(), snapshot_path="snap.json",
        ctx=ctx, sleeves=_sleeves(), generated_at="2026-06-07T09:00",
        data_dir=str(tmp_path), event_calendar_path=str(tmp_path / "none.yaml"),
    )
    assert run.gate["executable"] is True
    assert run.options.gated is False
