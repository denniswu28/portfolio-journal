"""Render an AdvisoryRun to markdown + JSON (ASCII-only, no box glyphs)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from src.advisory.models import AdvisoryRun, SEVERITY_ORDER

EDU_NOTE = (
    "_Educational planning note, not financial advice. Deterministic numbers from the "
    "harness; nothing auto-executes. Portfolio changes only via basket Method A/B._"
)


def _fmt_money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _safe_cell(value) -> str:
    """Sanitize free-text (LLM-sourced) for a markdown table cell: a literal '|'
    would inject extra columns and break the table, so swap it for '/'."""
    if not value:
        return "-"
    return str(value).replace("|", "/").replace("\n", " ").strip() or "-"


def render_markdown(run: AdvisoryRun) -> str:
    lines: List[str] = []
    a = lines.append

    a(f"# Daily Advisory - {run.as_of_date}")
    a("")
    a(EDU_NOTE)
    a("")

    # 0. Header + gate banner.
    a("## 0. Header")
    a(f"- Generated: {run.generated_at}")
    a(f"- Snapshot: {run.snapshot_path or 'n/a'}")
    a(f"- Portfolio value: {_fmt_money(run.portfolio_value)} | "
      f"Cash: {_fmt_money(run.cash)} ({run.cash_pct:.1f}%)")
    gate = run.gate or {}
    state = "EXECUTABLE" if gate.get("executable") else "ADVISORY ONLY - NOT EXECUTABLE"
    a(f"- **OPTIONS GATE: {state}** - {gate.get('reason', '')}")
    if run.thesis and run.thesis.found:
        stale = " (STALE vs snapshot)" if run.thesis.stale_vs_snapshot else ""
        a(f"- Thesis: {run.thesis.path}{stale}")
    a("")

    # 1. Priority action queue.
    actions = [al for al in run.rule_alerts if al.severity == "ACTION"]
    a("## 1. Priority action queue")
    if actions:
        a("| Category | Item | Detail |")
        a("|---|---|---|")
        for al in actions:
            a(f"| {al.category} | {al.title} | {al.detail} |")
    else:
        a("_No action-level rule breaches._")
    a("")

    # 2. All rule alerts.
    a("## 2. Portfolio rule alerts")
    a("| Severity | Category | Title |")
    a("|---|---|---|")
    for al in sorted(run.rule_alerts, key=lambda x: SEVERITY_ORDER.get(x.severity, 9)):
        a(f"| {al.severity} | {al.category} | {al.title} |")
    a("")

    # 3. Basket verdicts.
    cat_by_ticker = {it.ticker.upper(): it for it in (run.catalysts.items if run.catalysts else [])}
    a("## 3. Basket verdicts (add / trim / hold)")
    if run.basket_actions:
        a("| Basket | Weight | Band | Status | Verdict | Signal | Confidence | Catalyst | Note |")
        a("|---|---|---|---|---|---|---|---|---|")
        for c in run.basket_actions:
            band = (f"{c.band_min_pct}-{c.band_max_pct}%"
                    if c.band_min_pct is not None else "n/a")
            signal = "n/a" if c.signal_score is None else f"{c.signal_ticker} {c.signal_score:+.2f}"
            hit = cat_by_ticker.get((c.signal_ticker or "").upper())
            catalyst = hit.direction if hit else "-"
            a(f"| {c.basket} | {c.weight_pct:.1f}% | {band} | {c.band_status} | "
              f"**{c.verdict}** | {signal} | {c.confidence or '-'} | {catalyst} | {c.note} |")
        a("")
        a("_Verdict = policy band; Signal/Confidence = technical overlay (top holding). "
          "Apply via:_ `basket-plan --basket \"<name>\" --recompose ... | --resize-to <$>`.")
    else:
        a("_No baskets decomposed (no sleeve match or no positions)._")
    a("")

    # 4. Thesis overlay.
    a("## 4. Thesis overlay (narrative, advisory)")
    if run.thesis and run.thesis.found:
        a(f"**{run.thesis.title}**")
        a("")
        a("> " + run.thesis.digest.replace("\n", "\n> "))
        if run.thesis.tickers:
            a("")
            a(f"_Tickers mentioned:_ {', '.join(run.thesis.tickers)}")
    else:
        a("_No thesis found on or before the run date._")
    a("")

    # 4b. Daily catalysts (news bridge).
    a("## 4b. Daily catalysts (news bridge, advisory)")
    cat = run.catalysts
    if cat and cat.found:
        stale = " (STALE vs snapshot)" if cat.stale_vs_snapshot else ""
        a(f"_Source: {cat.generated_by or 'n/a'} | {cat.catalyst_date or 'n/a'}{stale}. "
          "Narrative/context only; deterministic numbers unchanged._")
        a("")
        if cat.macro:
            a("**Macro:**")
            a("| Direction | Summary | Date | Source |")
            a("|---|---|---|---|")
            for m in cat.macro:
                a(f"| {m.direction} | {_safe_cell(m.summary)} | {m.event_date or '-'} | "
                  f"{_safe_cell(m.source_url)} |")
            a("")
        if cat.items:
            a("**Per-ticker:**")
            a("| Ticker | Direction | Summary | Date | Confidence | Source |")
            a("|---|---|---|---|---|---|")
            for it in cat.items:
                a(f"| {it.ticker} | {it.direction} | {_safe_cell(it.summary)} | "
                  f"{it.event_date or '-'} | {it.confidence or '-'} | {_safe_cell(it.source_url)} |")
            a("")
        if cat.near_term:
            a("**Near-term catalysts (reduce size into events):** "
              + ", ".join(f"{it.ticker} ({it.event_date or '-'})" for it in cat.near_term))
            a("")
        if cat.freeform_notes:
            a("> " + cat.freeform_notes.replace("\n", "\n> "))
            a("")
    else:
        a("_No catalyst brief for the run date - run `catalyst-prompt` / `catalyst-ingest`._")
    a("")

    # 5. Event timing.
    a("## 5. Event calendar - when to act")
    if run.events:
        a("| Date | Label | Scope |")
        a("|---|---|---|")
        for ev in run.events:
            a(f"| {ev.get('date', '')} | {ev.get('label', '')} | {ev.get('scope', 'market')} |")
    else:
        a("_No events within the horizon._")
    a("")

    # 6. Options.
    a("## 6. Options - defined-risk ideas (gated)")
    opt = run.options
    if opt is None:
        a("_Options section skipped._")
    elif opt.gated:
        a(f"**SUPPRESSED label - {opt.gate_reason}**")
        a("")
        a("_Ideas below are advisory only; do not place until the gate clears._")
        a("")
        _render_option_candidates(a, opt.candidates, executable=False)
        _render_open_option_alerts(a, opt.open_position_alerts)
    else:
        _render_option_candidates(a, opt.candidates, executable=True)
        _render_open_option_alerts(a, opt.open_position_alerts)
    if opt and opt.note:
        a("")
        a(f"_{opt.note}_")
    a("")

    # 7. LLM advisory prompt.
    a("## 7. LLM advisory prompt (paste-ready, not executed)")
    a(f"- {run.prompt_path}" if run.prompt_path else "_Prompt not generated._")
    a("")

    # 8. Execution checklist.
    a("## 8. Execution checklist")
    for al in actions:
        a(f"- [ ] {al.title}")
    for c in run.basket_actions:
        if c.verdict in ("ADD", "TRIM"):
            a(f"- [ ] {c.verdict} {c.basket} (band {c.band_status})")
    a("- [ ] Run `monitor`; log fills with `log-trade` / `log-option`; `record-decision`.")
    if run.notes:
        a("")
        a("## Notes / degraded steps")
        for note in run.notes:
            a(f"- {note}")
    a("")
    return "\n".join(lines)


def _render_option_candidates(a, candidates, executable: bool):
    if not candidates:
        a("_No option candidates._")
        return
    label = "EXECUTABLE" if executable else "ADVISORY ONLY"
    a(f"| Underlying | Structure | Detail | Label |")
    a("|---|---|---|---|")
    for c in candidates:
        a(f"| {c.get('underlying', '')} | {c.get('structure', '')} | "
          f"{c.get('summary', '')} | {label} |")


def _render_open_option_alerts(a, alerts):
    if not alerts:
        return
    a("")
    a("**Open option positions - monitor alerts:**")
    a("")
    a("| Underlying | Kind | Severity | Message |")
    a("|---|---|---|---|")
    for al in alerts:
        a(f"| {al.get('underlying', '')} | {al.get('kind', '')} | "
          f"{al.get('severity', '')} | {al.get('message', '')} |")


def write_advisory(run: AdvisoryRun, md_path, json_path) -> Tuple[Path, Path]:
    md_path = Path(md_path)
    json_path = Path(json_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(run), encoding="utf-8")
    json_path.write_text(json.dumps(run.to_dict(), indent=2, default=str), encoding="utf-8")
    return md_path, json_path
