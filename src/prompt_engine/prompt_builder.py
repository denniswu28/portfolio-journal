"""
prompt_builder.py - Builds complete LLM prompts from portfolio state.

Contains:
  - Prompt templates (as Jinja2 template strings)
  - Context assembly logic
  - Main generate_prompt() function
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from jinja2 import Template

from src.data_ingestion.models import (
    FidelityAnalysisBundle,
    PerformanceMetrics,
    PersistentContext,
    PortfolioSnapshot,
    Trade,
)
from src.portfolio.reporting import (
    latest_time_weighted_periodic_return,
    summarize_asset_allocation,
    summarize_country_exposure,
    summarize_geographic_exposure,
    summarize_style_exposure,
)


# ── TEMPLATES ──────────────────────────────────────────────────────────────────

TRADE_INSTRUCTIONS_TEMPLATE = """
You are a portfolio management assistant for a real brokerage portfolio.

## Your Mandate
{{ strategy }}
Risk tolerance: {{ risk_tolerance }}
Investment horizon: {{ horizon }}
Hard constraints:
{% for c in constraints %}- {{ c }}
{% endfor %}
Personal rules:
{% for r in rules %}- {{ r }}
{% endfor %}

## Current Portfolio (as of {{ timestamp }})
Total Value: ${{ "%.2f"|format(total_value) }}
Cash: ${{ "%.2f"|format(cash) }} ({{ "%.1f"|format(cash_pct) }}%)
Cumulative Return: {{ "%+.2f"|format(cumulative_return) }}%

### Holdings
| Ticker | Shares | Avg Cost | Current | Unrealized P&L | Weight |
|--------|--------|----------|---------|----------------|--------|
{% for p in positions -%}
| {{ p.ticker }} | {{ p.shares }} | ${{ "%.2f"|format(p.avg_cost_basis) }} | ${{ "%.2f"|format(p.current_price) }} | {{ "%+.2f"|format(p.unrealized_pnl_pct) }}% | {{ "%.1f"|format(p.weight_pct) }}% |
{% endfor %}

{% if metrics %}
## Performance Metrics
- Sharpe Ratio: {{ metrics.sharpe_ratio if metrics.sharpe_ratio is not none else "N/A" }}
- Max Drawdown: {{ "%.2f"|format(metrics.max_drawdown_pct) }}%
- Win Rate: {{ "%.1f"|format(metrics.win_rate_pct) }}%
- Avg Win: {{ "%+.2f"|format(metrics.avg_win_pct) }}% | Avg Loss: {{ "%.2f"|format(metrics.avg_loss_pct) }}%
- Top-3 Concentration: {{ "%.1f"|format(metrics.concentration_top3_pct) }}%
{% endif %}

{% if analysis_summary %}
## Fidelity Exposure Context
{% if analysis_summary.asset_classes %}- Asset classes: {{ analysis_summary.asset_classes | join("; ") }}
{% endif %}{% if analysis_summary.regions %}- Regions: {{ analysis_summary.regions | join("; ") }}
{% endif %}{% if analysis_summary.countries %}- Countries: {{ analysis_summary.countries | join("; ") }}
{% endif %}{% if analysis_summary.styles %}- Style tilt: {{ analysis_summary.styles | join("; ") }}
{% endif %}{% if analysis_summary.periodic_return %}- Fidelity TWR as of {{ analysis_summary.periodic_return.period_end }}: YTD {{ analysis_summary.periodic_return.ytd }}, life {{ analysis_summary.periodic_return.life }}
{% endif %}
{% endif %}

## Recent Trades (Last {{ recent_trades|length }})
{% if recent_trades %}
{% for t in recent_trades -%}
- {{ t.timestamp.strftime("%Y-%m-%d") }}: {{ t.action }} {{ t.shares }} {{ t.ticker }} @ ${{ "%.2f"|format(t.price) }}
  Rationale: {{ t.rationale or "Not recorded" }}
{% endfor %}
{% else %}
No trades recorded yet.
{% endif %}
{% if basket_rows %}
## Baskets (weights vs policy bands)
| Basket | Weight | Band | Status |
|--------|--------|------|--------|
{% for b in basket_rows -%}
| {{ b.basket }} | {{ "%.1f"|format(b.weight_pct) }}% | {{ b.band_min_pct }}-{{ b.band_max_pct }}% | {{ b.band_status }} |
{% endfor %}
Change baskets only via Method A (recompose component %) or Method B (resize whole basket). Out-of-basket tickers are edited individually.
{% endif %}
{% if option_rows %}
## Open option positions (deterministic marks)
{% for o in option_rows -%}
- {{ o.underlying }} {{ o.structure }}: mark ${{ "%.2f"|format(o.mark) }}, P&L ${{ "%.2f"|format(o.pnl) }} ({{ "%.0f"|format(o.pnl_pct) }}%), {{ o.dte }} DTE
{% endfor %}
{% endif %}

## Your Task
{{ user_question }}

Respond with:
1. Assessment of current portfolio health and positioning
2. Specific trade recommendations (BUY/SELL, ticker, number of shares, order type)
3. Rationale for each recommendation
4. Position sizing guidance
5. Key risks to monitor

Do NOT violate the hard constraints listed above.
Format recommendations as clear, executable brokerage trade instructions.
""".strip()

REVIEW_TEMPLATE = """
You are a portfolio analyst reviewing a real brokerage portfolio.

## Investor Profile
Strategy: {{ strategy }}
Risk tolerance: {{ risk_tolerance }}
Investment horizon: {{ horizon }}
Constraints: {{ constraints | join(", ") }}

## Portfolio Snapshot ({{ timestamp }})
Total Value: ${{ "%.2f"|format(total_value) }}
Cash: ${{ "%.2f"|format(cash) }} ({{ "%.1f"|format(cash_pct) }}%)
Cumulative Return: {{ "%+.2f"|format(cumulative_return) }}%

### Holdings
| Ticker | Shares | Avg Cost | Current | Unrealized P&L | Weight |
|--------|--------|----------|---------|----------------|--------|
{% for p in positions -%}
| {{ p.ticker }} | {{ p.shares }} | ${{ "%.2f"|format(p.avg_cost_basis) }} | ${{ "%.2f"|format(p.current_price) }} | {{ "%+.2f"|format(p.unrealized_pnl_pct) }}% | {{ "%.1f"|format(p.weight_pct) }}% |
{% endfor %}

{% if metrics %}
## Performance Summary
- Cumulative Return: {{ "%+.2f"|format(metrics.cumulative_return_pct) }}%
- Sharpe Ratio: {{ metrics.sharpe_ratio if metrics.sharpe_ratio is not none else "N/A" }}
- Max Drawdown: -{{ "%.2f"|format(metrics.max_drawdown_pct) }}%
- Win Rate: {{ "%.1f"|format(metrics.win_rate_pct) }}% ({{ metrics.winning_trades }}W / {{ metrics.losing_trades }}L)
{% endif %}

{% if analysis_summary %}
## Fidelity Exposure Context
{% if analysis_summary.asset_classes %}- Asset classes: {{ analysis_summary.asset_classes | join("; ") }}
{% endif %}{% if analysis_summary.regions %}- Regions: {{ analysis_summary.regions | join("; ") }}
{% endif %}{% if analysis_summary.countries %}- Countries: {{ analysis_summary.countries | join("; ") }}
{% endif %}{% if analysis_summary.styles %}- Style tilt: {{ analysis_summary.styles | join("; ") }}
{% endif %}{% if analysis_summary.periodic_return %}- Fidelity TWR as of {{ analysis_summary.periodic_return.period_end }}: YTD {{ analysis_summary.periodic_return.ytd }}, life {{ analysis_summary.periodic_return.life }}
{% endif %}
{% endif %}

## Review Request
{{ user_question }}

Please provide:
1. Portfolio strengths and weaknesses
2. Sector/concentration analysis
3. Risk assessment
4. Specific improvement suggestions
5. Performance vs benchmark commentary
""".strip()

RISK_CHECK_TEMPLATE = """
You are a risk manager reviewing a real brokerage portfolio.

## Portfolio ({{ timestamp }})
Total Value: ${{ "%.2f"|format(total_value) }}
Cash: ${{ "%.2f"|format(cash) }} ({{ "%.1f"|format(cash_pct) }}%)
Constraints: {{ constraints | join(" | ") }}

### Current Positions
{% for p in positions -%}
{{ p.ticker }}: {{ p.shares }} shares @ ${{ "%.2f"|format(p.current_price) }}, 
  Cost: ${{ "%.2f"|format(p.avg_cost_basis) }}, P&L: {{ "%+.2f"|format(p.unrealized_pnl_pct) }}%, 
  Weight: {{ "%.1f"|format(p.weight_pct) }}%
{% endfor %}

{% if metrics %}
Risk Metrics:
- Max Drawdown: -{{ "%.2f"|format(metrics.max_drawdown_pct) }}%
- Top-3 Concentration: {{ "%.1f"|format(metrics.concentration_top3_pct) }}%
- Win Rate: {{ "%.1f"|format(metrics.win_rate_pct) }}%
{% endif %}

{% if analysis_summary %}
Fidelity Exposure Context:
{% if analysis_summary.asset_classes %}- Asset classes: {{ analysis_summary.asset_classes | join("; ") }}
{% endif %}{% if analysis_summary.regions %}- Regions: {{ analysis_summary.regions | join("; ") }}
{% endif %}{% if analysis_summary.countries %}- Countries: {{ analysis_summary.countries | join("; ") }}
{% endif %}{% if analysis_summary.styles %}- Style tilt: {{ analysis_summary.styles | join("; ") }}
{% endif %}{% if analysis_summary.periodic_return %}- Fidelity TWR as of {{ analysis_summary.periodic_return.period_end }}: YTD {{ analysis_summary.periodic_return.ytd }}, life {{ analysis_summary.periodic_return.life }}
{% endif %}
{% endif %}

## Risk Review Request
{{ user_question }}

Identify:
1. Positions approaching stop-loss levels (-15%)
2. Constraint violations (position sizing, cash reserve, etc.)
3. Concentration risks
4. Positions to monitor closely
5. Recommended defensive actions
""".strip()

OPTIONS_PROMPT_TEMPLATE = """
You are an options strategy assistant for a real brokerage portfolio (Level 2 + margin).

## Mandate & Rules
{{ strategy }}
Risk tolerance: {{ risk_tolerance }} | Horizon: {{ horizon }}
Hard constraints:
{% for c in constraints %}- {{ c }}
{% endfor %}
Options policy: Level 2 + margin. Allowed: buy-writes, covered calls (+roll), long calls/puts, cash-secured puts, long straddles/strangles, spreads <= 4 legs, covered puts. FORBIDDEN: naked calls / undefined-risk short legs. Every order must be fully specified (underlying, right, strike(s), expiry/DTE, structure, action+direction per leg, net debit/credit, contracts/"hands", max loss/profit, breakevens, margin/assignment, exit rules). All pricing, greeks, payoff, and probabilities are deterministic (QuantLib); treat them as ground truth and never invent option numbers.

## Portfolio ({{ timestamp }})
Total Value: ${{ "%.2f"|format(total_value) }} | Cash: ${{ "%.2f"|format(cash) }} ({{ "%.1f"|format(cash_pct) }}%)
{% if basket_rows %}
### Baskets (weights vs policy bands)
| Basket | Weight | Band | Status |
|--------|--------|------|--------|
{% for b in basket_rows -%}
| {{ b.basket }} | {{ "%.1f"|format(b.weight_pct) }}% | {{ b.band_min_pct }}-{{ b.band_max_pct }}% | {{ b.band_status }} |
{% endfor %}
{% endif %}
{% if option_rows %}
### Open option positions (deterministic marks)
| Underlying | Structure | Mark | P&L | P&L% | DTE |
|---|---|---|---|---|---|
{% for o in option_rows -%}
| {{ o.underlying }} | {{ o.structure }} | ${{ "%.2f"|format(o.mark) }} | ${{ "%.2f"|format(o.pnl) }} | {{ "%.0f"|format(o.pnl_pct) }}% | {{ o.dte }} |
{% endfor %}
{% endif %}
{% if risk_summary %}
### Option book risk
- Net delta: {{ "%.1f"|format(risk_summary.net_delta) }} (dollar-delta ${{ "%.0f"|format(risk_summary.dollar_delta) }})
- Net theta/day: ${{ "%.0f"|format(risk_summary.net_theta) }} | Net vega/vol-pt: ${{ "%.0f"|format(risk_summary.net_vega) }}
- Options sleeve: {{ "%.1f"|format(risk_summary.sleeve_weight_pct) }}% (status {{ risk_summary.sleeve_status }})
{% endif %}

## Task
{{ user_question }}

Respond with fully-specified, Level-2-compliant option orders only (or HOLD). For each order give:
1. Structure and legs (underlying, right, strike, expiry/DTE)
2. Net debit/credit and contracts ("hands")
3. Max loss/profit, breakevens, probability of profit
4. Margin / buying-power and assignment notes
5. Exit rules: take-profit, stop-loss, and time stop
Never propose naked calls or undefined-risk short legs. Defer to the deterministic numbers above.
""".strip()


TEMPLATES = {
    "trade": TRADE_INSTRUCTIONS_TEMPLATE,
    "review": REVIEW_TEMPLATE,
    "risk": RISK_CHECK_TEMPLATE,
    "options": OPTIONS_PROMPT_TEMPLATE,
}


# ── CONTEXT ASSEMBLY ──────────────────────────────────────────────────────────

def _build_context(
    snapshot: PortfolioSnapshot,
    recent_trades: List[Trade],
    metrics: Optional[PerformanceMetrics],
    persistent_ctx: PersistentContext,
    question: str,
    analysis_bundle: Optional[FidelityAnalysisBundle] = None,
    basket_rows: Optional[List[dict]] = None,
    option_rows: Optional[List[dict]] = None,
    risk_summary: Optional[dict] = None,
) -> dict:
    """Assemble all Jinja2 template variables into a single context dict."""
    cash_pct = (
        (snapshot.cash / snapshot.total_portfolio_value) * 100
        if snapshot.total_portfolio_value
        else 0.0
    )

    return {
        "strategy": persistent_ctx.investment_strategy,
        "risk_tolerance": persistent_ctx.risk_tolerance,
        "horizon": persistent_ctx.investment_horizon,
        "constraints": persistent_ctx.constraints,
        "rules": persistent_ctx.rules,
        "timestamp": snapshot.timestamp.strftime("%Y-%m-%d %H:%M ET"),
        "total_value": snapshot.total_portfolio_value,
        "cash": snapshot.cash,
        "cash_pct": cash_pct,
        "cumulative_return": snapshot.cumulative_return_pct,
        "positions": snapshot.positions,
        "metrics": metrics,
        "analysis_summary": _build_analysis_summary(analysis_bundle),
        "recent_trades": recent_trades,
        "user_question": question,
        "basket_rows": basket_rows,
        "option_rows": option_rows,
        "risk_summary": risk_summary,
    }


def _build_analysis_summary(analysis_bundle: Optional[FidelityAnalysisBundle]) -> Optional[dict]:
    if not analysis_bundle:
        return None

    periodic = latest_time_weighted_periodic_return(analysis_bundle)
    return {
        "asset_classes": _format_summary_items(
            summarize_asset_allocation(analysis_bundle.asset_allocation, limit=5)
        ),
        "regions": _format_summary_items(
            summarize_geographic_exposure(analysis_bundle.geographic_exposure, limit=5)
        ),
        "countries": _format_summary_items(
            summarize_country_exposure(analysis_bundle.geographic_exposure, limit=5)
        ),
        "styles": _format_summary_items(
            summarize_style_exposure(analysis_bundle.style_exposure, limit=5)
        ),
        "periodic_return": {
            "period_end": periodic.period_end_date.isoformat(),
            "ytd": _format_optional_pct(periodic.ytd_pct),
            "life": _format_optional_pct(periodic.life_pct),
        }
        if periodic
        else None,
    }


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def generate_prompt(
    prompt_type: str,
    snapshot: PortfolioSnapshot,
    recent_trades: List[Trade],
    metrics: Optional[PerformanceMetrics],
    persistent_ctx: PersistentContext,
    user_question: str,
    analysis_bundle: Optional[FidelityAnalysisBundle] = None,
    basket_rows: Optional[List[dict]] = None,
    option_rows: Optional[List[dict]] = None,
    risk_summary: Optional[dict] = None,
) -> str:
    """
    Render a fully formatted LLM prompt ready to paste into any AI assistant.

    Args:
        prompt_type: One of "trade", "review", or "risk".
        snapshot: Current portfolio snapshot.
        recent_trades: List of recent Trade objects to include.
        metrics: Optional PerformanceMetrics for enhanced prompts.
        persistent_ctx: User strategy and constraints from config YAML.
        user_question: The specific question or task for the LLM.

    Returns:
        A rendered prompt string.

    Raises:
        KeyError: If prompt_type is not one of the supported types.
    """
    if prompt_type not in TEMPLATES:
        raise KeyError(
            f"Unknown prompt type '{prompt_type}'. "
            f"Available: {list(TEMPLATES.keys())}"
        )

    template_str = TEMPLATES[prompt_type]
    context = _build_context(
        snapshot,
        recent_trades,
        metrics,
        persistent_ctx,
        user_question,
        analysis_bundle=analysis_bundle,
        basket_rows=basket_rows,
        option_rows=option_rows,
        risk_summary=risk_summary,
    )
    template = Template(template_str)
    return template.render(**context)


def _format_summary_items(rows: list[dict[str, object]]) -> list[str]:
    return [f"{row['name']} {float(row['weight_pct']):.1f}%" for row in rows]


def _format_optional_pct(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"
