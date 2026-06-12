from src.advisory.models import CatalystItem, MacroCatalyst, CatalystContext, AdvisoryRun


def test_catalyst_models_defaults_and_to_dict():
    item = CatalystItem(ticker="NVDA", direction="bull", summary="new order")
    assert item.event_date is None and item.confidence == ""
    assert item.to_dict()["ticker"] == "NVDA"

    macro = MacroCatalyst(direction="bear", summary="hot CPI")
    assert macro.event_date is None and macro.source_url is None
    assert macro.to_dict()["direction"] == "bear"

    ctx = CatalystContext()
    assert ctx.found is False and ctx.items == [] and ctx.macro == []
    assert ctx.to_dict()["found"] is False


def test_advisory_run_has_catalysts_default():
    run = AdvisoryRun(
        as_of_date="2026-06-12", generated_at="t", snapshot_path=None,
        portfolio_value=0.0, cash=0.0, cash_pct=0.0, gate={},
    )
    assert run.catalysts.found is False
    assert "catalysts" in run.to_dict()
