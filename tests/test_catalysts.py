from src.advisory.models import CatalystItem, MacroCatalyst, CatalystContext, AdvisoryRun
import pytest
from src.advisory.catalysts import parse_catalyst_paste, CatalystValidationError

GOOD_PASTE = """```yaml
as_of: 2026-06-12
generated_by: perplexity
macro:
  - summary: May CPI cooler than expected
    direction: bull
    event_date: 2026-06-11
items:
  - ticker: nvda
    direction: bull
    summary: New hyperscaler order
    event_date: 2026-06-18
    confidence: med
    source_url: https://example.com/n
    notes: watch profit-taking
  - ticker: MU
    direction: bear
    summary: DRAM pricing softness
freeform_notes: |
  Risk-on tape.
```"""


def test_parse_good_paste():
    ctx, warnings = parse_catalyst_paste(GOOD_PASTE)
    assert warnings == []
    assert ctx.catalyst_date == "2026-06-12"
    assert ctx.generated_by == "perplexity"
    assert [i.ticker for i in ctx.items] == ["NVDA", "MU"]   # upper-cased
    assert ctx.items[0].direction == "bull"
    assert ctx.items[0].confidence == "med"
    assert len(ctx.macro) == 1 and ctx.macro[0].direction == "bull"
    assert "Risk-on" in ctx.freeform_notes


def test_parse_skips_bad_blocks_with_warnings():
    paste = """
items:
  - ticker: NVDA
    direction: sideways    # invalid enum -> skip
    summary: x
  - direction: bull        # missing ticker -> skip
    summary: y
  - ticker: AAPL
    direction: bull
    summary: good one
"""
    ctx, warnings = parse_catalyst_paste(paste)
    assert [i.ticker for i in ctx.items] == ["AAPL"]
    assert len(warnings) == 2


def test_parse_coerces_bad_optional_fields():
    paste = """
items:
  - ticker: NVDA
    direction: bull
    summary: x
    confidence: extreme        # invalid optional -> coerced to ""
    event_date: not-a-date     # invalid optional -> None
"""
    ctx, warnings = parse_catalyst_paste(paste)
    assert ctx.items[0].confidence == ""
    assert ctx.items[0].event_date is None
    assert len(warnings) == 2


def test_parse_nothing_usable_raises():
    with pytest.raises(CatalystValidationError):
        parse_catalyst_paste("items:\n  - direction: bull\n    summary: no ticker\n")


def test_parse_malformed_yaml_raises_clean_error():
    # A syntactically broken paste must surface CatalystValidationError, not a raw
    # yaml.YAMLError traceback (tolerant-parsing contract for the human-paste workflow).
    with pytest.raises(CatalystValidationError):
        parse_catalyst_paste("items:\n  - {ticker: NVDA, direction: bull, summary: 'unclosed\n")


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


from datetime import date
from src.advisory.catalysts import find_latest_catalyst_file, build_catalyst_context


def _write_brief(dir_path, d, body):
    cat_dir = dir_path / "catalysts"
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / f"catalyst-{d}.yaml").write_text(body, encoding="utf-8")


def test_find_latest_on_or_before(tmp_path):
    _write_brief(tmp_path, "2026-06-10", "items:\n  - {ticker: A, direction: bull, summary: x}\n")
    _write_brief(tmp_path, "2026-06-12", "items:\n  - {ticker: B, direction: bull, summary: y}\n")
    found = find_latest_catalyst_file(date(2026, 6, 11), data_dir=tmp_path)
    assert found is not None and found.name == "catalyst-2026-06-10.yaml"


def test_build_context_missing_is_graceful(tmp_path):
    ctx = build_catalyst_context(date(2026, 6, 12), data_dir=tmp_path)
    assert ctx.found is False


def test_build_context_near_term_and_staleness(tmp_path):
    _write_brief(tmp_path, "2026-06-10", (
        "as_of: 2026-06-10\n"
        "items:\n"
        "  - {ticker: NVDA, direction: bull, summary: earnings, event_date: 2026-06-12}\n"
        "  - {ticker: MU, direction: bear, summary: far, event_date: 2026-09-01}\n"
    ))
    ctx = build_catalyst_context(
        date(2026, 6, 11), data_dir=tmp_path,
        snapshot_date=date(2026, 6, 11), event_horizon_days=30,
    )
    assert ctx.found is True
    assert [i.ticker for i in ctx.near_term] == ["NVDA"]   # within 30d; MU excluded
    assert ctx.stale_vs_snapshot is True                   # 06-10 < snapshot 06-11
