"""
baskets.py - First-class basket model and the two sanctioned portfolio-change methods.

The portfolio may only be changed through baskets in two ways:

  * Method A - recompose (intra-basket): change the component ratios / percentages
    inside a basket. The basket's total dollar value is held fixed unless an explicit
    new total is supplied.
  * Method B - resize: scale the whole basket up or down by adding or removing
    dollars, preserving the (possibly just-recomposed) component ratios.

Individual funds and out-of-basket tickers (e.g. FXAIX, SPAXX) are edited directly,
outside this engine, and are reported separately.

Baskets are sourced canonically from the Fidelity CSV "Basket Name" column
(`Position.basket_name`). Strategic policy bands (min/max weight) and thesis come
from the sleeve universe in `config/growth_universe.yaml`, matched to each basket by
holdings overlap. Mismatches are surfaced, never silently reconciled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.data_ingestion.models import PortfolioSnapshot, Position
from src.portfolio.optimizer import SleeveDefinition


# Recognized portfolio-change methods.
METHOD_RECOMPOSE = "recompose"  # Method A
METHOD_RESIZE = "resize"        # Method B

# Default minimum trade size (dollars) below which an order is suppressed.
DEFAULT_MIN_TRADE_DOLLARS = 25.0

# Cash-like / non-tradeable rows that never belong to a basket.
_CASH_LIKE = {"SPAXX", "SPAXX**", "FCASH", "CORE", "PENDING ACTIVITY"}


# ── DATA STRUCTURES ──────────────────────────────────────────────────────────

@dataclass
class BasketComponent:
    """One holding inside a basket."""

    ticker: str
    company_name: str
    shares: float
    price: float
    market_value: float
    weight_in_basket_pct: float
    unrealized_pnl_pct: float = 0.0


@dataclass
class Basket:
    """A named group of holdings (from the Fidelity Basket Name column)."""

    name: str
    components: List[BasketComponent] = field(default_factory=list)
    total_value: float = 0.0
    weight_in_portfolio_pct: float = 0.0
    # Matched policy band from the sleeve universe (None when unmatched).
    sleeve_name: Optional[str] = None
    proxy: Optional[str] = None
    role: str = ""
    band_min_pct: Optional[float] = None
    band_max_pct: Optional[float] = None
    notes: str = ""

    @property
    def tickers(self) -> List[str]:
        return [c.ticker for c in self.components]

    def component(self, ticker: str) -> Optional[BasketComponent]:
        token = ticker.upper().strip()
        for comp in self.components:
            if comp.ticker == token:
                return comp
        return None

    def band_status(self, weight_pct: Optional[float] = None) -> str:
        """Classify a weight against the policy band ('OK'/'ABOVE'/'BELOW'/'N/A')."""
        weight = self.weight_in_portfolio_pct if weight_pct is None else weight_pct
        if self.band_min_pct is None or self.band_max_pct is None:
            return "N/A"
        if weight > self.band_max_pct + 1e-9:
            return "ABOVE"
        if weight < self.band_min_pct - 1e-9:
            return "BELOW"
        return "OK"


@dataclass
class BasketView:
    """The full basket decomposition of a snapshot."""

    baskets: List[Basket] = field(default_factory=list)
    out_of_basket: List[Position] = field(default_factory=list)
    cash: float = 0.0
    portfolio_value: float = 0.0
    mismatches: List[str] = field(default_factory=list)

    def get(self, name: str) -> Optional[Basket]:
        token = name.strip().lower()
        for basket in self.baskets:
            if basket.name.strip().lower() == token:
                return basket
        return None


@dataclass
class OrderInstruction:
    """A single brokerage-ready instruction produced by a basket method."""

    action: str  # "BUY" | "SELL" | "HOLD"
    ticker: str
    basket: str
    dollars: float            # absolute trade size, >= 0
    shares: float             # estimated shares, >= 0
    price: float              # reference price
    order_type: str           # "DAY LIMIT" | "NAV"
    current_dollars: float
    target_dollars: float
    current_pct: float        # weight within basket, before
    target_pct: float         # weight within basket, after
    note: str = ""


@dataclass
class BasketRebalancePlan:
    """The result of applying Method A or Method B to one basket."""

    basket: str
    method: str
    current_total: float
    target_total: float
    portfolio_value: float
    orders: List[OrderInstruction] = field(default_factory=list)
    sleeve_name: Optional[str] = None
    band_min_pct: Optional[float] = None
    band_max_pct: Optional[float] = None
    band_status_before: str = "N/A"
    band_status_after: str = "N/A"
    notes: List[str] = field(default_factory=list)

    @property
    def net_cash(self) -> float:
        """Net cash freed (positive) or deployed (negative) by this plan."""
        freed = sum(o.dollars for o in self.orders if o.action == "SELL")
        spent = sum(o.dollars for o in self.orders if o.action == "BUY")
        return freed - spent


# ── BUILD / RECONCILE ────────────────────────────────────────────────────────

def _is_cash_like(ticker: str) -> bool:
    token = ticker.upper().strip()
    return token in _CASH_LIKE or token.endswith("**")


def match_sleeve(basket_tickers: Iterable[str], sleeves: List[SleeveDefinition]) -> Optional[SleeveDefinition]:
    """
    Match a basket to its best sleeve by holdings overlap (deterministic).

    Returns the sleeve sharing the most tickers with the basket, or None when
    there is no overlap at all.
    """
    holdings = {t.upper().strip() for t in basket_tickers}
    best: Optional[SleeveDefinition] = None
    best_overlap = 0
    for sleeve in sleeves:
        overlap = len(holdings & sleeve.aliases)
        if overlap > best_overlap:
            best_overlap = overlap
            best = sleeve
    return best if best_overlap > 0 else None


def build_baskets(
    snapshot: PortfolioSnapshot,
    sleeves: Optional[List[SleeveDefinition]] = None,
) -> BasketView:
    """
    Decompose a snapshot into baskets, out-of-basket holdings, and policy bands.

    Args:
        snapshot: The enriched portfolio snapshot (positions carry basket_name).
        sleeves: Sleeve universe for policy bands; if None, bands are omitted.

    Returns:
        A populated BasketView.
    """
    sleeves = sleeves or []
    portfolio_value = snapshot.total_portfolio_value or (
        snapshot.invested_value + snapshot.cash
    )

    grouped: Dict[str, List[Position]] = {}
    out_of_basket: List[Position] = []
    for pos in snapshot.positions:
        if _is_cash_like(pos.ticker):
            continue
        name = (pos.basket_name or "").strip()
        if not name:
            out_of_basket.append(pos)
        else:
            grouped.setdefault(name, []).append(pos)

    baskets: List[Basket] = []
    mismatches: List[str] = []
    # Build a ticker -> sleeve map once for mismatch detection.
    alias_to_sleeve: Dict[str, str] = {}
    for sleeve in sleeves:
        for alias in sleeve.aliases:
            alias_to_sleeve.setdefault(alias, sleeve.name)

    for name, members in sorted(grouped.items()):
        total = sum(p.market_value for p in members)
        components: List[BasketComponent] = []
        for pos in sorted(members, key=lambda p: p.market_value, reverse=True):
            weight_in_basket = (pos.market_value / total * 100) if total else 0.0
            components.append(
                BasketComponent(
                    ticker=pos.ticker,
                    company_name=pos.company_name,
                    shares=pos.shares,
                    price=pos.current_price,
                    market_value=pos.market_value,
                    weight_in_basket_pct=weight_in_basket,
                    unrealized_pnl_pct=pos.unrealized_pnl_pct,
                )
            )

        sleeve = match_sleeve([c.ticker for c in components], sleeves)
        basket = Basket(
            name=name,
            components=components,
            total_value=total,
            weight_in_portfolio_pct=(total / portfolio_value * 100) if portfolio_value else 0.0,
            sleeve_name=sleeve.name if sleeve else None,
            proxy=sleeve.proxy if sleeve else None,
            role=sleeve.role if sleeve else "",
            band_min_pct=sleeve.min_weight_pct if sleeve else None,
            band_max_pct=sleeve.max_weight_pct if sleeve else None,
            notes=sleeve.notes if sleeve else "",
        )
        baskets.append(basket)

        # Flag components whose strongest sleeve disagrees with this basket's sleeve.
        if sleeve is not None:
            for comp in components:
                mapped = alias_to_sleeve.get(comp.ticker)
                if mapped is not None and mapped != sleeve.name:
                    mismatches.append(
                        f"{comp.ticker} in basket '{name}' maps to sleeve '{mapped}', "
                        f"not the basket's matched sleeve '{sleeve.name}'"
                    )

    return BasketView(
        baskets=baskets,
        out_of_basket=out_of_basket,
        cash=snapshot.cash,
        portfolio_value=portfolio_value,
        mismatches=mismatches,
    )


def compute_basket_metrics(view: BasketView) -> List[dict]:
    """Per-basket summary rows for reporting (value, weight, P&L, band status)."""
    rows: List[dict] = []
    for basket in view.baskets:
        cost = sum(
            c.market_value / (1 + c.unrealized_pnl_pct / 100)
            for c in basket.components
            if (1 + c.unrealized_pnl_pct / 100) != 0
        )
        pnl = basket.total_value - cost if cost else 0.0
        rows.append(
            {
                "basket": basket.name,
                "sleeve": basket.sleeve_name or "",
                "value": round(basket.total_value, 2),
                "weight_pct": round(basket.weight_in_portfolio_pct, 2),
                "holdings": len(basket.components),
                "band_min_pct": basket.band_min_pct,
                "band_max_pct": basket.band_max_pct,
                "band_status": basket.band_status(),
                "unrealized_pnl": round(pnl, 2),
                "top_holding": basket.components[0].ticker if basket.components else "",
            }
        )
    return rows


# ── METHOD A: RECOMPOSE (intra-basket) ───────────────────────────────────────

def recompose_basket(
    basket: Basket,
    target_weights: Dict[str, float],
    portfolio_value: float,
    prices: Optional[Dict[str, float]] = None,
    new_total: Optional[float] = None,
    min_trade_dollars: float = DEFAULT_MIN_TRADE_DOLLARS,
) -> BasketRebalancePlan:
    """
    Method A - change component percentages inside a basket.

    Args:
        basket: The basket to recompose.
        target_weights: Mapping of ticker -> target percent of the basket. Need not
            sum to exactly 100; it is normalized. Tickers not present are added
            (price must be supplied via ``prices`` or already on the basket).
        portfolio_value: Total portfolio value, for band checks.
        prices: Reference prices for tickers not already in the basket.
        new_total: Override the basket total. Defaults to the current total
            (pure recomposition with no net cash change).
        min_trade_dollars: Orders smaller than this are suppressed to HOLD.

    Returns:
        A BasketRebalancePlan with one OrderInstruction per affected ticker.
    """
    prices = {k.upper(): v for k, v in (prices or {}).items()}
    target_total = basket.total_value if new_total is None else float(new_total)

    normalized = _normalize_weights(target_weights)
    tickers = sorted(set(normalized) | set(basket.tickers))

    orders: List[OrderInstruction] = []
    notes: List[str] = []
    for ticker in tickers:
        comp = basket.component(ticker)
        current_dollars = comp.market_value if comp else 0.0
        target_pct = normalized.get(ticker, 0.0)
        target_dollars = target_pct / 100.0 * target_total
        price = comp.price if comp else prices.get(ticker, 0.0)
        if price <= 0 and target_dollars > 0:
            notes.append(f"No price for {ticker}; cannot size the add - supply a price.")
            continue
        order = _build_order(
            ticker=ticker,
            basket=basket.name,
            current_dollars=current_dollars,
            target_dollars=target_dollars,
            price=price,
            basket_current_total=basket.total_value,
            basket_target_total=target_total,
            min_trade_dollars=min_trade_dollars,
        )
        orders.append(order)

    plan = BasketRebalancePlan(
        basket=basket.name,
        method=METHOD_RECOMPOSE,
        current_total=basket.total_value,
        target_total=target_total,
        portfolio_value=portfolio_value,
        orders=orders,
        sleeve_name=basket.sleeve_name,
        band_min_pct=basket.band_min_pct,
        band_max_pct=basket.band_max_pct,
        band_status_before=basket.band_status(),
        band_status_after=basket.band_status(
            (target_total / portfolio_value * 100) if portfolio_value else None
        ),
        notes=notes,
    )
    _annotate_band(plan)
    return plan


# ── METHOD B: RESIZE (whole-basket) ──────────────────────────────────────────

def resize_basket(
    basket: Basket,
    portfolio_value: float,
    new_total: Optional[float] = None,
    delta_dollars: Optional[float] = None,
    min_trade_dollars: float = DEFAULT_MIN_TRADE_DOLLARS,
) -> BasketRebalancePlan:
    """
    Method B - scale the whole basket up or down, preserving component ratios.

    Provide exactly one of ``new_total`` or ``delta_dollars``.

    Args:
        basket: The basket to resize.
        portfolio_value: Total portfolio value, for band checks.
        new_total: The desired post-resize basket total.
        delta_dollars: Dollars to add (positive) or remove (negative).
        min_trade_dollars: Orders smaller than this are suppressed to HOLD.

    Returns:
        A BasketRebalancePlan scaling every component proportionally.
    """
    if (new_total is None) == (delta_dollars is None):
        raise ValueError("Provide exactly one of new_total or delta_dollars.")

    current_total = basket.total_value
    if new_total is None:
        target_total = current_total + float(delta_dollars)
    else:
        target_total = float(new_total)
    target_total = max(target_total, 0.0)

    scale = (target_total / current_total) if current_total else 0.0

    orders: List[OrderInstruction] = []
    for comp in basket.components:
        target_dollars = comp.market_value * scale
        order = _build_order(
            ticker=comp.ticker,
            basket=basket.name,
            current_dollars=comp.market_value,
            target_dollars=target_dollars,
            price=comp.price,
            basket_current_total=current_total,
            basket_target_total=target_total,
            min_trade_dollars=min_trade_dollars,
        )
        orders.append(order)

    plan = BasketRebalancePlan(
        basket=basket.name,
        method=METHOD_RESIZE,
        current_total=current_total,
        target_total=target_total,
        portfolio_value=portfolio_value,
        orders=orders,
        sleeve_name=basket.sleeve_name,
        band_min_pct=basket.band_min_pct,
        band_max_pct=basket.band_max_pct,
        band_status_before=basket.band_status(),
        band_status_after=basket.band_status(
            (target_total / portfolio_value * 100) if portfolio_value else None
        ),
    )
    _annotate_band(plan)
    return plan


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    cleaned = {k.upper().strip(): float(v) for k, v in weights.items() if float(v) >= 0}
    total = sum(cleaned.values())
    if total <= 0:
        return cleaned
    return {k: (v / total * 100.0) for k, v in cleaned.items()}


def _order_type_for(ticker: str) -> str:
    """Mutual funds (5-letter symbols ending in X) trade at NAV; everything else day-limit."""
    token = ticker.upper().strip()
    if len(token) == 5 and token.endswith("X") and token.isalpha():
        return "NAV"
    return "DAY LIMIT"


def _build_order(
    ticker: str,
    basket: str,
    current_dollars: float,
    target_dollars: float,
    price: float,
    basket_current_total: float,
    basket_target_total: float,
    min_trade_dollars: float,
) -> OrderInstruction:
    delta = target_dollars - current_dollars
    current_pct = (current_dollars / basket_current_total * 100) if basket_current_total else 0.0
    target_pct = (target_dollars / basket_target_total * 100) if basket_target_total else 0.0
    shares = (abs(delta) / price) if price > 0 else 0.0

    if abs(delta) < min_trade_dollars:
        action, note = "HOLD", "Below min trade size; no action."
        trade_dollars, trade_shares = 0.0, 0.0
    elif delta > 0:
        action, note = "BUY", ""
        trade_dollars, trade_shares = delta, shares
    else:
        action, note = "SELL", ""
        trade_dollars, trade_shares = abs(delta), shares

    return OrderInstruction(
        action=action,
        ticker=ticker,
        basket=basket,
        dollars=round(trade_dollars, 2),
        shares=round(trade_shares, 3),
        price=round(price, 4),
        order_type=_order_type_for(ticker),
        current_dollars=round(current_dollars, 2),
        target_dollars=round(target_dollars, 2),
        current_pct=round(current_pct, 2),
        target_pct=round(target_pct, 2),
        note=note,
    )


def _annotate_band(plan: BasketRebalancePlan) -> None:
    if plan.band_status_after == "ABOVE":
        plan.notes.append(
            f"Post-change basket weight exceeds the {plan.band_max_pct:.1f}% policy ceiling "
            f"for sleeve '{plan.sleeve_name}'."
        )
    elif plan.band_status_after == "BELOW":
        plan.notes.append(
            f"Post-change basket weight is below the {plan.band_min_pct:.1f}% policy floor "
            f"for sleeve '{plan.sleeve_name}'."
        )


# ── MARKDOWN ORDER PLAN ──────────────────────────────────────────────────────

def _fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def write_basket_order_plan(
    plans: List[BasketRebalancePlan],
    view: BasketView,
    output_path: str | Path,
    title: Optional[str] = None,
    context_notes: Optional[List[str]] = None,
) -> Path:
    """
    Render basket rebalance plans to a brokerage-ready markdown order plan.

    The output mirrors the existing hand-written order_plan format: one section per
    basket, an order table, band checks, and a net-cash reconciliation.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: List[str] = []
    lines.append(f"# {title or 'Basket Order Plan'}")
    lines.append("")
    lines.append(
        "Educational planning note, not financial advice. The portfolio is changed "
        "only through baskets, by **Method A (recompose)** - changing component "
        "percentages inside a basket - or **Method B (resize)** - adding/removing "
        "dollars to/from a whole basket. Individual out-of-basket tickers are handled "
        "separately at the end."
    )
    lines.append("")
    lines.append(f"- Generated: {generated}")
    lines.append(f"- Portfolio value: {_fmt_usd(view.portfolio_value)}")
    lines.append(f"- Cash: {_fmt_usd(view.cash)}")
    lines.append("")

    if context_notes:
        lines.append("## Context")
        lines.append("")
        for note in context_notes:
            lines.append(f"- {note}")
        lines.append("")

    total_freed = 0.0
    total_spent = 0.0

    for plan in plans:
        method_label = "Method A - recompose (intra-basket)" if plan.method == METHOD_RECOMPOSE else "Method B - resize (whole-basket)"
        lines.append(f"## {plan.basket} - {method_label}")
        lines.append("")
        lines.append(f"- Matched sleeve: {plan.sleeve_name or 'unmatched'}")
        if plan.band_min_pct is not None and plan.band_max_pct is not None:
            lines.append(
                f"- Policy band: {plan.band_min_pct:.1f}% - {plan.band_max_pct:.1f}% "
                f"(before: {plan.band_status_before}, after: {plan.band_status_after})"
            )
        lines.append(
            f"- Basket total: {_fmt_usd(plan.current_total)} -> {_fmt_usd(plan.target_total)} "
            f"(net {_fmt_usd(plan.target_total - plan.current_total)})"
        )
        lines.append("")
        lines.append("| Action | Ticker | Order type | Current $ | Target $ | Trade $ | Est. shares | Basket % (cur -> tgt) |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|")
        for o in plan.orders:
            lines.append(
                f"| {o.action} | {o.ticker} | {o.order_type} | {_fmt_usd(o.current_dollars)} | "
                f"{_fmt_usd(o.target_dollars)} | {_fmt_usd(o.dollars)} | {o.shares:.3f} | "
                f"{_fmt_pct(o.current_pct)} -> {_fmt_pct(o.target_pct)} |"
            )
        lines.append("")
        freed = sum(o.dollars for o in plan.orders if o.action == "SELL")
        spent = sum(o.dollars for o in plan.orders if o.action == "BUY")
        total_freed += freed
        total_spent += spent
        lines.append(f"- Cash freed by sells: {_fmt_usd(freed)} | deployed by buys: {_fmt_usd(spent)} | net: {_fmt_usd(freed - spent)}")
        for note in plan.notes:
            lines.append(f"- Note: {note}")
        lines.append("")

    lines.append("## Net cash reconciliation")
    lines.append("")
    lines.append(f"- Total freed by sells: {_fmt_usd(total_freed)}")
    lines.append(f"- Total deployed by buys: {_fmt_usd(total_spent)}")
    lines.append(f"- Net cash impact: {_fmt_usd(total_freed - total_spent)}")
    lines.append("")

    if view.out_of_basket:
        lines.append("## Out-of-basket holdings (handled individually, not via baskets)")
        lines.append("")
        lines.append("| Ticker | Value | Weight |")
        lines.append("|---|---:|---:|")
        for pos in sorted(view.out_of_basket, key=lambda p: p.market_value, reverse=True):
            weight = (pos.market_value / view.portfolio_value * 100) if view.portfolio_value else 0.0
            lines.append(f"| {pos.ticker} | {_fmt_usd(pos.market_value)} | {_fmt_pct(weight)} |")
        lines.append("")

    if view.mismatches:
        lines.append("## Basket / sleeve mismatches to review")
        lines.append("")
        for mismatch in view.mismatches:
            lines.append(f"- {mismatch}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
