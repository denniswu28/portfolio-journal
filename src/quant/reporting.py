"""Markdown + PNG writers for quant artifacts.

ASCII-only output (no box-drawing glyphs) for Windows console/file safety. Every
writer creates parent directories and returns the written Path.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pandas as pd

EDU_NOTE = "_Educational tooling, not financial advice. Deterministic; nothing auto-executes._"


def _ensure_parent(path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fmt(value, suffix="", nd=2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _metrics_table(metrics, label="Strategy") -> List[str]:
    row = metrics.as_row()
    lines = [
        f"| Metric | {label} |",
        "|---|---|",
        f"| Total return | {_fmt(row['total_return_pct'], '%')} |",
        f"| CAGR | {_fmt(row['cagr_pct'], '%')} |",
        f"| Ann. volatility | {_fmt(row['ann_volatility_pct'], '%')} |",
        f"| Sharpe | {_fmt(row['sharpe'])} |",
        f"| Sortino | {_fmt(row['sortino'])} |",
        f"| Max drawdown | {_fmt(row['max_drawdown_pct'], '%')} |",
        f"| Calmar | {_fmt(row['calmar'])} |",
        f"| Hit rate | {_fmt(row['hit_rate_pct'], '%')} |",
        f"| Periods | {row['n_periods']} |",
        f"| Avg turnover | {_fmt(row['turnover_pct'], '%')} |",
    ]
    return lines


# ── SIGNALS ──────────────────────────────────────────────────────────────────

def write_signals_report(signal_sets, output_path, title="Technical Signals") -> Path:
    path = _ensure_parent(output_path)
    lines = [f"# {title}", "", EDU_NOTE, "",
             "| Ticker | Close | Trend | Momentum | Overall | RSI | RV%ile | Flags |",
             "|---|---|---|---|---|---|---|---|"]
    for s in signal_sets:
        comp = s.composite or {}
        lines.append(
            "| {t} | {c} | {tr} | {mo} | {ov} | {rsi} | {rv} | {fl} |".format(
                t=s.ticker,
                c=_fmt(s.close),
                tr=_fmt(comp.get("trend"), nd=2),
                mo=_fmt(comp.get("momentum"), nd=2),
                ov=_fmt(comp.get("overall"), nd=2),
                rsi=_fmt((s.momentum or {}).get("rsi"), nd=1),
                rv=_fmt((s.volatility or {}).get("rv_percentile"), nd=2),
                fl=", ".join(s.flags) if s.flags else "-",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── BACKTEST ─────────────────────────────────────────────────────────────────

def write_backtest_report(result, output_path, title="Backtest") -> Path:
    path = _ensure_parent(output_path)
    lines = [f"# {title}: {result.strategy}", "", EDU_NOTE, "",
             f"- Params: `{result.params}`",
             f"- Periods/year: {result.periods_per_year}",
             f"- Trades recorded: {len(result.trades)}", ""]
    lines += _metrics_table(result.metrics, label=result.strategy)
    if result.benchmark_metrics is not None:
        lines += ["", "## Benchmark", ""]
        lines += _metrics_table(result.benchmark_metrics, label="Benchmark")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_walk_forward_report(wf, output_path, title="Walk-Forward (OOS)") -> Path:
    path = _ensure_parent(output_path)
    lines = [f"# {title}", "", EDU_NOTE, "",
             f"- Folds: {len(wf.folds)}", ""]
    lines += _metrics_table(wf.metrics, label="Stitched OOS")
    lines += ["", "## Folds", "",
              "| # | Train end | Test start | Test end | OOS Sharpe | OOS return |",
              "|---|---|---|---|---|---|"]
    for i, fold in enumerate(wf.folds, 1):
        m = fold.result.metrics
        lines.append(
            f"| {i} | {fold.train_end.date()} | {fold.test_start.date()} | "
            f"{fold.test_end.date()} | {_fmt(m.sharpe)} | {_fmt(m.total_return_pct, '%')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── OPTIMIZE ─────────────────────────────────────────────────────────────────

def write_optimize_report(wfo, output_path, title="Walk-Forward Parameter Optimization") -> Path:
    path = _ensure_parent(output_path)
    warn = "**OVERFIT WARNING**" if wfo.overfit_warning else "no strong overfit signal"
    lines = [f"# {title}", "", EDU_NOTE, "",
             f"- Scorer: {wfo.scorer}",
             f"- Mean in-sample score: {_fmt(wfo.mean_is_score, nd=3)}",
             f"- Mean out-of-sample score: {_fmt(wfo.mean_oos_score, nd=3)}",
             f"- IS - OOS gap: {_fmt(wfo.is_oos_gap, nd=3)} ({warn})", ""]
    lines += _metrics_table(wfo.oos_metrics, label="Stitched OOS")
    lines += ["", "## Parameter stability (times chosen across folds)", ""]
    for param, counts in wfo.param_stability.items():
        rendered = ", ".join(f"{value}: {count}" for value, count in sorted(counts.items()))
        lines.append(f"- **{param}**: {rendered}")
    lines += ["", "## Folds", "",
              "| # | Test window | Chosen params | IS score | OOS score |",
              "|---|---|---|---|---|"]
    for i, fold in enumerate(wfo.folds, 1):
        lines.append(
            f"| {i} | {fold.test_start.date()}..{fold.test_end.date()} | "
            f"`{fold.chosen_params}` | {_fmt(fold.is_score, nd=3)} | {_fmt(fold.oos_score, nd=3)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── FACTOR ───────────────────────────────────────────────────────────────────

def write_factor_report(model, exposures_df, decomposition, output_path,
                        title="Factor / Risk Report") -> Path:
    path = _ensure_parent(output_path)
    lines = [f"# {title}", "", EDU_NOTE, "",
             f"- Factors: {', '.join(model.factors)}", "",
             "## Per-asset betas", "",
             "| Asset | " + " | ".join(model.factors) + " | R2 |",
             "|---|" + "---|" * (len(model.factors) + 1)]
    for asset in exposures_df.index:
        betas = " | ".join(_fmt(exposures_df.loc[asset, f]) for f in model.factors)
        r2 = _fmt(model.r_squared.get(asset))
        lines.append(f"| {asset} | {betas} | {r2} |")
    if decomposition is not None:
        lines += ["", "## Portfolio variance decomposition", "",
                  f"- Systematic: {_fmt(decomposition['systematic_pct'], '%')}",
                  f"- Specific: {_fmt(decomposition['specific_pct'], '%')}", "",
                  "| Factor | % of total variance |", "|---|---|"]
        for factor, pct in decomposition.get("per_factor_pct", {}).items():
            lines.append(f"| {factor} | {_fmt(pct, '%')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── OPTIONS BACKTEST ─────────────────────────────────────────────────────────

def write_options_backtest_report(result, output_path,
                                  title="Option Structure Backtest") -> Path:
    path = _ensure_parent(output_path)
    s = result.summary
    lines = [f"# {title}: {result.ticker} {result.structure}", "", EDU_NOTE, "",
             f"> {result.note}", "",
             "| Metric | Value |", "|---|---|",
             f"| Trades | {s.get('n_trades', 0)} |",
             f"| Win rate | {_fmt(s.get('win_rate_pct'), '%')} |",
             f"| Total P&L | {_fmt(s.get('total_pnl'))} |",
             f"| Total return | {_fmt(s.get('total_return_pct'), '%')} |",
             f"| Avg P&L / trade | {_fmt(s.get('avg_pnl'))} |",
             f"| Avg credit | {_fmt(s.get('avg_credit'))} |",
             f"| Avg return on risk | {_fmt(s.get('avg_return_on_risk_pct'), '%')} |",
             f"| Worst trade | {_fmt(s.get('worst_trade'))} |",
             f"| Avg hold days | {_fmt(s.get('avg_hold_days'), nd=1)} |", ""]
    if result.trades:
        lines += ["## Trades", "",
                  "| Entry | Exit | Short K | Credit | P&L | Reason |",
                  "|---|---|---|---|---|---|"]
        for t in result.trades:
            lines.append(
                f"| {t.entry_date.date()} | {t.exit_date.date()} | {_fmt(t.short_strike)} | "
                f"{_fmt(t.entry_credit)} | {_fmt(t.pnl)} | {t.exit_reason} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── PLOTS ────────────────────────────────────────────────────────────────────

def plot_equity_curve(equity: pd.Series, output_path, benchmark: Optional[pd.Series] = None,
                      title="Equity Curve") -> Optional[Path]:
    """Save a simple equity-curve PNG. Returns None if matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    path = _ensure_parent(output_path)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(equity.index, equity.values, label="Strategy", linewidth=1.5)
    if benchmark is not None and not pd.Series(benchmark).dropna().empty:
        ax.plot(benchmark.index, benchmark.values, label="Benchmark", linewidth=1.0, alpha=0.7)
    ax.set_title(title)
    ax.set_ylabel("Equity")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path
