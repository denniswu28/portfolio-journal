"""Tests for advisory markdown + JSON rendering."""

import json

from src.advisory.models import (
    AdvisoryRun,
    BasketActionCandidate,
    OptionAdvisorySummary,
    RuleAlert,
    ThesisContext,
)
from src.advisory.reporting import render_markdown, write_advisory


def _run(gated=True):
    return AdvisoryRun(
        as_of_date="2026-06-07",
        generated_at="2026-06-07T09:00:00",
        snapshot_path="data/snap.json",
        portfolio_value=17700.0,
        cash=1800.0,
        cash_pct=10.2,
        gate={"executable": not gated, "reason": "Level-2 pending." if gated else "active"},
        rule_alerts=[
            RuleAlert("ACTION", "position_cap", "AAPL exceeds 10% cap", "Trim AAPL.", ticker="AAPL"),
            RuleAlert("INFO", "allocation", "Allocation snapshot", "Long 80%."),
        ],
        basket_actions=[
            BasketActionCandidate("AI Platform", 16.0, 4, 12, "ABOVE", "TRIM", "Above band."),
        ],
        thesis=ThesisContext(path="data/boist-2026-06-07.md", thesis_date="2026-06-07",
                             title="Memory Supercycle", digest="Shortage to 2030.",
                             tickers=["SNDK", "MU"], found=True),
        events=[{"date": "2026-06-25", "label": "MU earnings", "scope": "MU"}],
        options=OptionAdvisorySummary(
            gated=gated, gate_reason="Level-2 pending.",
            candidates=[{"underlying": "SMH", "structure": "bull-put-spread",
                         "summary": "535/515, POP 69%"}],
            open_position_alerts=[{"underlying": "SMH", "kind": "TIME_STOP",
                                   "severity": "WARN", "message": "21 DTE"}],
            note="theoretical",
        ),
        metrics={"sharpe_ratio": 1.2},
        prompt_path="output/prompts/p.txt",
        notes=["yfinance skipped (--no-network)."],
    )


def test_render_markdown_has_sections_and_gate_banner():
    md = render_markdown(_run(gated=True))
    assert "# Daily Advisory — 2026-06-07" in md
    assert "ADVISORY ONLY — NOT EXECUTABLE" in md
    assert "## 1. Priority action queue" in md
    assert "AAPL exceeds 10% cap" in md
    assert "## 3. Basket verdicts" in md and "**TRIM**" in md
    assert "Memory Supercycle" in md
    assert "MU earnings" in md
    # Gated options are shown but labeled advisory only (label, don't hide).
    assert "SUPPRESSED label" in md and "SMH" in md


def test_render_markdown_executable_when_ungated():
    md = render_markdown(_run(gated=False))
    assert "OPTIONS GATE: EXECUTABLE" in md


def test_write_advisory_emits_md_and_json(tmp_path):
    run = _run()
    md_path, json_path = write_advisory(run, tmp_path / "a.md", tmp_path / "a.json")
    assert md_path.exists() and json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["as_of_date"] == "2026-06-07"
    # Alerts are sorted action-first in the JSON.
    assert data["rule_alerts"][0]["severity"] == "ACTION"


def test_no_box_drawing_glyphs():
    md = render_markdown(_run())
    for glyph in ["─", "│", "┌", "┐", "└", "┘", "├", "┤", "═"]:
        assert glyph not in md
