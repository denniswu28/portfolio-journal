"""Backtest defined-risk option structures on reconstructed theoretical prices.

yfinance exposes only the *current* option chain, so we do NOT depend on historical
chains. Instead we take historical spot, estimate volatility from trailing realized
vol (optionally scaled by an IV/RV premium), select strikes by %-OTM, build the
structure with the existing Level-2 builders, and price/mark every leg with the same
QuantLib harness used live (``analyze_strategy`` / ``mark_strategy``). Exit discipline
(TP / SL / time-stop / expiry) follows AGENTS.md §2.

Results are theoretical (no bid/ask, slippage, or vol skew) and labeled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

from src.data_ingestion.market_data import (
    get_price_history,
    get_risk_free_rate,
    realized_volatility_from_prices,
)
from src.options.strategies import (
    analyze_strategy,
    bull_put_spread,
    cash_secured_put,
    mark_strategy,
    validate_level2,
)
from src.quant.models import compute_backtest_metrics

VOL_FLOOR = 0.10
CONTRACT_MULTIPLIER = 100


@dataclass
class OptionBacktestConfig:
    structure: str = "cash-secured-put"   # or "bull-put-spread"
    dte: int = 30
    otm: float = 0.05                      # short strike fraction OTM
    width_pct: float = 0.05                # spread width as fraction of spot
    contracts: int = 1
    take_profit_pct: float = 50.0          # % of max profit (credit captured)
    stop_loss_pct: float = 100.0           # % of premium base
    close_by_dte: int = 7
    iv_rv_premium: float = 1.2             # premium of implied over realized vol
    vol_window: int = 20
    entry_freq_days: int = 7
    american: bool = True


@dataclass
class OptionBacktestTrade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    structure: str
    short_strike: float
    expiry: date
    entry_credit: float          # positive credit received
    max_loss: float              # position dollars (negative)
    risk_capital: float
    exit_reason: str
    pnl: float
    hold_days: int
    return_on_risk_pct: float


@dataclass
class OptionBacktestResult:
    ticker: str
    structure: str
    trades: List[OptionBacktestTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    summary: dict = field(default_factory=dict)
    metrics: Optional[object] = None
    note: str = "Theoretical prices (no slippage/skew); educational, not financial advice."


def _build_structure(cfg: OptionBacktestConfig, ticker: str, spot: float, expiry: date):
    short_strike = round(spot * (1.0 - cfg.otm), 2)
    if cfg.structure == "cash-secured-put":
        return cash_secured_put(ticker, short_strike, expiry, cfg.contracts), short_strike
    if cfg.structure == "bull-put-spread":
        long_strike = round(short_strike - cfg.width_pct * spot, 2)
        return bull_put_spread(ticker, short_strike, long_strike, expiry, cfg.contracts), short_strike
    raise ValueError(f"Unsupported structure for backtest: {cfg.structure}")


def _entry_vol(prices: pd.Series, as_of: pd.Timestamp, cfg: OptionBacktestConfig) -> float:
    trailing = prices.loc[:as_of].tail(cfg.vol_window + 1)
    rv = realized_volatility_from_prices(trailing, periods_per_year=252)
    vol = (rv or 0.0) * cfg.iv_rv_premium
    return max(vol, VOL_FLOOR)


def backtest_option_structure(
    ticker: str,
    cfg: OptionBacktestConfig = OptionBacktestConfig(),
    prices: Optional[pd.Series] = None,
    period: str = "3y",
    rate: Optional[float] = None,
    initial_capital: float = 100_000.0,
) -> OptionBacktestResult:
    """Backtest one defined-risk structure rolled through history on one underlying."""
    ticker = ticker.upper().strip()
    if prices is None:
        history = get_price_history([ticker], period=period, interval="1d")
        prices = history[ticker] if ticker in history.columns else pd.Series(dtype=float)
    prices = pd.Series(prices).astype(float).dropna()
    if len(prices) < cfg.vol_window + cfg.dte // 2 + 2:
        return OptionBacktestResult(ticker=ticker, structure=cfg.structure)

    rate = get_risk_free_rate() if rate is None else rate

    trades: List[OptionBacktestTrade] = []
    capital = initial_capital
    equity_index: List[pd.Timestamp] = []
    equity_values: List[float] = []

    open_state = None           # dict while a position is open
    last_entry_pos = -10**9

    index = prices.index
    for pos, ts in enumerate(index):
        spot = float(prices.iloc[pos])
        eval_d = ts.date()

        # ── manage an open position ──
        if open_state is not None:
            strat = open_state["strategy"]
            vol = open_state["vol"]
            expiry = open_state["expiry"]
            entry_debit = open_state["entry_debit"]   # negative for credit
            target_profit = open_state["target_profit"]
            premium_base = open_state["premium_base"]
            mark = mark_strategy(strat, spot, rate, vol, eval_date=eval_d, american=cfg.american)
            pnl = mark - entry_debit
            dte_left = (expiry - eval_d).days

            exit_reason = None
            if pnl >= cfg.take_profit_pct / 100.0 * target_profit:
                exit_reason = "take_profit"
            elif pnl <= -cfg.stop_loss_pct / 100.0 * premium_base:
                exit_reason = "stop_loss"
            elif dte_left <= cfg.close_by_dte:
                exit_reason = "time_stop"
            elif eval_d >= expiry:
                exit_reason = "expiry"

            if exit_reason is not None:
                capital += pnl
                trades.append(OptionBacktestTrade(
                    entry_date=open_state["entry_date"], exit_date=ts,
                    structure=cfg.structure, short_strike=open_state["short_strike"],
                    expiry=expiry, entry_credit=open_state["credit"],
                    max_loss=open_state["max_loss"], risk_capital=open_state["risk_capital"],
                    exit_reason=exit_reason, pnl=float(pnl),
                    hold_days=(ts - open_state["entry_date"]).days,
                    return_on_risk_pct=float(pnl / open_state["risk_capital"] * 100.0)
                    if open_state["risk_capital"] else 0.0,
                ))
                open_state = None
                equity_index.append(ts)
                equity_values.append(capital)
                continue
            equity_index.append(ts)
            equity_values.append(capital + pnl)
            continue

        # ── consider opening a new position ──
        can_enter = pos >= cfg.vol_window and (pos - last_entry_pos) >= cfg.entry_freq_days
        if can_enter:
            expiry = eval_d + timedelta(days=cfg.dte)
            try:
                strat, short_strike = _build_structure(cfg, ticker, spot, expiry)
                if validate_level2(strat):
                    raise ValueError("not Level-2 compliant")
                vol = _entry_vol(prices, ts, cfg)
                analysis = analyze_strategy(strat, spot, rate, vol, eval_date=eval_d,
                                            american=cfg.american)
            except (ValueError, KeyError):
                equity_index.append(ts)
                equity_values.append(capital)
                continue

            entry_debit = analysis.net_debit            # negative for a credit
            credit = max(0.0, -entry_debit)
            max_loss = analysis.max_loss                # negative position dollars
            if cfg.structure == "cash-secured-put":
                risk_capital = short_strike * CONTRACT_MULTIPLIER * cfg.contracts - credit
            else:
                risk_capital = abs(max_loss)
            open_state = {
                "strategy": strat, "vol": vol, "expiry": expiry,
                "entry_date": ts, "short_strike": short_strike,
                "entry_debit": entry_debit, "credit": credit, "max_loss": max_loss,
                "target_profit": max(analysis.max_profit, 1e-9),
                "premium_base": max(credit, abs(entry_debit), 1e-9),
                "risk_capital": max(risk_capital, 1e-9),
            }
            last_entry_pos = pos

        equity_index.append(ts)
        equity_values.append(capital)

    equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_index))
    metrics = compute_backtest_metrics(equity_curve, periods_per_year=252) if len(equity_curve) > 2 else None
    summary = _summarize(trades, initial_capital)
    return OptionBacktestResult(
        ticker=ticker, structure=cfg.structure, trades=trades,
        equity_curve=equity_curve, summary=summary, metrics=metrics,
    )


def _summarize(trades: List[OptionBacktestTrade], initial_capital: float) -> dict:
    if not trades:
        return {"n_trades": 0}
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    return {
        "n_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2),
        "total_pnl": round(float(sum(pnls)), 2),
        "total_return_pct": round(float(sum(pnls) / initial_capital * 100.0), 4),
        "avg_pnl": round(float(np.mean(pnls)), 2),
        "avg_credit": round(float(np.mean([t.entry_credit for t in trades])), 2),
        "worst_trade": round(float(min(pnls)), 2),
        "best_trade": round(float(max(pnls)), 2),
        "avg_hold_days": round(float(np.mean([t.hold_days for t in trades])), 1),
        "avg_return_on_risk_pct": round(float(np.mean([t.return_on_risk_pct for t in trades])), 3),
    }
