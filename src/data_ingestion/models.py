"""
models.py - Pydantic data models for the portfolio tracker.

Defines the core data structures used throughout the application:
- RawPosition / RawPortfolioData  (output of paste/CSV parsers)
- Trade                           (individual trade records)
- Position                        (enriched with live quotes)
- PortfolioSnapshot               (point-in-time portfolio state)
- PersistentContext               (user strategy & constraints)
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ── RAW INGESTION MODELS ────────────────────────────────────────────────────

class RawPosition(BaseModel):
    """A position as parsed directly from the Investopedia paste or CSV."""

    ticker: str
    company_name: str
    current_price: float
    day_change: float = 0.0
    day_change_pct: float = 0.0
    cost_basis_per_share: float
    shares: int
    market_value: float
    gain_loss: float = 0.0
    gain_loss_pct: float = 0.0

    @field_validator("ticker")
    @classmethod
    def ticker_must_be_uppercase(cls, v: str) -> str:
        return v.upper().strip()


class RawPortfolioData(BaseModel):
    """Portfolio data as parsed from the raw source (before live-quote enrichment)."""

    total_value: float
    cash: float = 0.0
    today_change: float = 0.0
    today_change_pct: float = 0.0
    total_gain_loss: float = 0.0
    total_gain_loss_pct: float = 0.0
    positions: List[RawPosition] = Field(default_factory=list)
    parsed_at: datetime = Field(default_factory=datetime.now)


# ── TRADE MODELS ─────────────────────────────────────────────────────────────

class Trade(BaseModel):
    """A single logged trade with metadata and rationale."""

    id: str = ""
    ticker: str
    action: str  # "BUY" or "SELL"
    shares: int
    price: float
    total_value: float = 0.0
    rationale: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    tags: List[str] = Field(default_factory=list)

    @field_validator("action")
    @classmethod
    def action_must_be_valid(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in ("BUY", "SELL"):
            raise ValueError(f"action must be BUY or SELL, got '{v}'")
        return v

    @field_validator("ticker")
    @classmethod
    def ticker_must_be_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    def model_post_init(self, __context) -> None:
        if not self.total_value:
            self.total_value = self.shares * self.price
        if not self.id:
            self.id = f"{self.ticker}_{self.action}_{self.timestamp.strftime('%Y%m%d%H%M%S')}"


# ── ENRICHED POSITION MODEL ──────────────────────────────────────────────────

class Position(BaseModel):
    """
    A portfolio position enriched with analytics fields.
    Derived from RawPosition + trade history + live quotes.
    """

    ticker: str
    company_name: str
    shares: int
    avg_cost_basis: float
    current_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    weight_pct: float = 0.0
    day_change: float = 0.0
    day_change_pct: float = 0.0

    def model_post_init(self, __context) -> None:
        if not self.market_value:
            self.market_value = self.shares * self.current_price
        if not self.unrealized_pnl:
            self.unrealized_pnl = (self.current_price - self.avg_cost_basis) * self.shares
        if not self.unrealized_pnl_pct and self.avg_cost_basis:
            self.unrealized_pnl_pct = (
                (self.current_price / self.avg_cost_basis) - 1
            ) * 100


# ── SNAPSHOT MODEL ───────────────────────────────────────────────────────────

class PortfolioSnapshot(BaseModel):
    """A complete point-in-time snapshot of the portfolio."""

    timestamp: datetime = Field(default_factory=datetime.now)
    total_portfolio_value: float
    cash: float = 0.0
    invested_value: float = 0.0
    today_change: float = 0.0
    today_change_pct: float = 0.0
    total_gain_loss: float = 0.0
    total_gain_loss_pct: float = 0.0
    cumulative_return_pct: float = 0.0
    positions: List[Position] = Field(default_factory=list)
    recorded_metrics: Optional["PerformanceMetrics"] = None

    def model_post_init(self, __context) -> None:
        if not self.invested_value:
            self.invested_value = sum(p.market_value for p in self.positions)


# ── PERSISTENT CONTEXT MODEL ─────────────────────────────────────────────────

class PersistentContext(BaseModel):
    """User-defined investment strategy and constraints loaded from YAML."""

    investment_strategy: str = ""
    risk_tolerance: str = "Moderate"
    investment_horizon: str = "6 months"
    constraints: List[str] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)
    benchmark: str = "SPY"
    notes: str = ""


# ── PERFORMANCE METRICS MODEL ────────────────────────────────────────────────

class PerformanceMetrics(BaseModel):
    """Computed performance metrics for the portfolio."""

    cumulative_return_pct: float = 0.0
    daily_returns: List[float] = Field(default_factory=list)
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    max_drawdown_date: Optional[str] = None
    current_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    concentration_top3_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    # Benchmark-relative fields (populated when benchmark history is available)
    benchmark_ticker: str = "SPY"
    benchmark_cumulative_return_pct: Optional[float] = None
    alpha_annualized_pct: Optional[float] = None
    beta: Optional[float] = None
