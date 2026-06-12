"""
reporting.py - Brokerage-ready option order tickets and monitor alert rendering.

Every option order ticket emits the full specification required by AGENTS.md:
underlying, right, strikes, expiry/DTE, structure, action+direction per leg, net
debit/credit, contracts, max loss/profit, breakevens, margin/assignment, and exit
rules. A ticket missing any field is not executable.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Optional

from src.options.models import BUY, OptionStrategy
from src.options.pricing import time_to_expiry
from src.options.strategies import StrategyAnalysis, validate_level2


def _fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def estimate_buying_power(strategy: OptionStrategy, analysis: StrategyAnalysis) -> float:
    """
    Estimate the buying-power / margin reserved (dollars).

    Defined-risk debit and vertical structures reserve their max loss. A genuine
    cash-secured put (a short put with no protective long put) reserves
    strike * 100 * contracts. Covered structures secured by stock reserve no
    additional cash here.
    """
    from src.options.models import SECURED_CASH, SECURED_SHORT_STOCK, SECURED_STOCK

    if strategy.secured_by in (SECURED_STOCK, SECURED_SHORT_STOCK):
        return 0.0

    short_puts = [leg for leg in strategy.legs if leg.is_put() and leg.action != BUY]
    long_puts = [leg for leg in strategy.legs if leg.is_put() and leg.action == BUY]

    # A short put covered by a long put is a defined-risk spread: reserve the max loss,
    # never the full cash-secured notional, even if flagged secured-by-cash.
    if short_puts and long_puts:
        return abs(analysis.max_loss)

    # Genuine cash-secured put(s): reserve the full strike notional.
    if strategy.secured_by == SECURED_CASH and short_puts:
        return sum(leg.strike * leg.multiplier * leg.contracts for leg in short_puts)

    return abs(analysis.max_loss)


def order_ticket_lines(
    strategy: OptionStrategy,
    analysis: StrategyAnalysis,
    spot: float,
    rate: float,
    eval_date: Optional[date] = None,
) -> List[str]:
    """Build the fully-specified option order ticket as markdown lines."""
    ev = eval_date or date.today()
    nearest = min(leg.expiry for leg in strategy.legs)
    dte = (nearest - ev).days
    violations = validate_level2(strategy)
    is_debit = analysis.net_debit > 0
    limit_label = "net debit (pay <=)" if is_debit else "net credit (collect >=)"
    limit_value = abs(analysis.net_debit)
    contracts = max((leg.contracts for leg in strategy.legs), default=1)
    buying_power = estimate_buying_power(strategy, analysis)

    lines: List[str] = []
    lines.append(f"### {strategy.underlying} {strategy.name} ({contracts} hand{'s' if contracts != 1 else ''})")
    lines.append("")
    if violations:
        lines.append("> **REJECTED — not Level-2 compliant:**")
        for v in violations:
            lines.append(f"> - {v}")
        lines.append("")
    lines.append(f"- Underlying: **{strategy.underlying}** (spot {_fmt_usd(spot)})")
    lines.append(f"- Structure: **{strategy.name}**")
    lines.append(f"- Expiry: **{nearest.isoformat()}** ({dte} DTE)")
    lines.append("- Legs:")
    for leg in strategy.legs:
        lines.append(
            f"  - {leg.action} {leg.contracts} {strategy.underlying} "
            f"{leg.expiry.isoformat()} {leg.strike:g} {leg.right}"
            + (f" @ ~{leg.entry_price:.2f}" if leg.entry_price is not None else "")
        )
    lines.append(f"- Order: **{limit_label} {_fmt_usd(limit_value)}** for {contracts} hand(s)")
    lines.append(f"- Contracts (hands): **{contracts}**")
    lines.append(f"- Max profit: **{_fmt_usd(analysis.max_profit)}** | Max loss: **{_fmt_usd(analysis.max_loss)}**")
    if analysis.risk_reward is not None:
        lines.append(f"- Reward/risk: **{analysis.risk_reward:.2f}**")
    be = ", ".join(f"{b:g}" for b in analysis.breakevens) or "none in range"
    lines.append(f"- Breakeven(s): **{be}**")
    if analysis.pop is not None:
        lines.append(f"- Probability of profit (risk-neutral): **{analysis.pop * 100:.1f}%**")
    lines.append(
        f"- Net greeks: delta {analysis.net_delta:g}, gamma {analysis.net_gamma:g}, "
        f"theta/day {_fmt_usd(analysis.net_theta)}, vega {_fmt_usd(analysis.net_vega)}/vol-pt, "
        f"rho {_fmt_usd(analysis.net_rho)}/1%"
    )
    lines.append(f"- Buying power / margin reserved: **{_fmt_usd(buying_power)}**")
    lines.append(
        "- Assignment risk: short legs can be assigned when in-the-money, especially near "
        "ex-dividend dates and at expiry."
    )
    lines.append("- Exit rules:")
    if is_debit:
        lines.append("  - Take profit: close near **+50%** of the structure's value.")
        lines.append("  - Stop loss: close at **-50%** of the entry debit.")
        lines.append("  - Time stop: close by **14-21 DTE**; do not hold into expiration week.")
    else:
        lines.append("  - Take profit: buy back at **~50%** of max profit (collected credit).")
        lines.append("  - Stop loss: defend or roll if loss reaches **~1x the credit** or a short strike is breached.")
        lines.append("  - Time stop: close/roll by **14-21 DTE**; reduce size into known events.")
    lines.append("")
    return lines


def write_option_ticket(
    strategy: OptionStrategy,
    analysis: StrategyAnalysis,
    spot: float,
    rate: float,
    output_path: str | Path,
    eval_date: Optional[date] = None,
    title: Optional[str] = None,
    context_notes: Optional[List[str]] = None,
) -> Path:
    """Write a fully-specified option order ticket to markdown."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines.append(f"# {title or 'Option Order Ticket'}")
    lines.append("")
    lines.append(
        "Educational planning note, not financial advice. Prices and greeks are "
        "deterministic (QuantLib); confirm exact strikes and limits against the live chain."
    )
    lines.append("")
    if context_notes:
        for note in context_notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(order_ticket_lines(strategy, analysis, spot, rate, eval_date))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
