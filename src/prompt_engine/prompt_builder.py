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

from src.data_ingestion.models import PerformanceMetrics, PortfolioSnapshot, Trade, PersistentContext


# ── TEMPLATES ──────────────────────────────────────────────────────────────────

TRADE_INSTRUCTIONS_TEMPLATE = """
You are a portfolio management assistant for a paper trading account
on Investopedia Simulator.

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

## Recent Trades (Last {{ recent_trades|length }})
{% if recent_trades %}
{% for t in recent_trades -%}
- {{ t.timestamp.strftime("%Y-%m-%d") }}: {{ t.action }} {{ t.shares }} {{ t.ticker }} @ ${{ "%.2f"|format(t.price) }}
  Rationale: {{ t.rationale or "Not recorded" }}
{% endfor %}
{% else %}
No trades recorded yet.
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
Format recommendations as executable Investopedia Simulator instructions.
""".strip()

REVIEW_TEMPLATE = """
You are a portfolio analyst reviewing a paper trading account on Investopedia Simulator.

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
You are a risk manager reviewing a paper trading portfolio on Investopedia Simulator.

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

## Risk Review Request
{{ user_question }}

Identify:
1. Positions approaching stop-loss levels (-15%)
2. Constraint violations (position sizing, cash reserve, etc.)
3. Concentration risks
4. Positions to monitor closely
5. Recommended defensive actions
""".strip()

TEMPLATES = {
    "trade": TRADE_INSTRUCTIONS_TEMPLATE,
    "review": REVIEW_TEMPLATE,
    "risk": RISK_CHECK_TEMPLATE,
}


# ── CONTEXT ASSEMBLY ──────────────────────────────────────────────────────────

def _build_context(
    snapshot: PortfolioSnapshot,
    recent_trades: List[Trade],
    metrics: Optional[PerformanceMetrics],
    persistent_ctx: PersistentContext,
    question: str,
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
        "recent_trades": recent_trades,
        "user_question": question,
    }


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def generate_prompt(
    prompt_type: str,
    snapshot: PortfolioSnapshot,
    recent_trades: List[Trade],
    metrics: Optional[PerformanceMetrics],
    persistent_ctx: PersistentContext,
    user_question: str,
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
    context = _build_context(snapshot, recent_trades, metrics, persistent_ctx, user_question)
    template = Template(template_str)
    return template.render(**context)
