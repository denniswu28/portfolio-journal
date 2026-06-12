"""
models.py - Pydantic data models for the portfolio tracker.

Defines the core data structures used throughout the application:
- RawPosition / RawPortfolioData  (output of CSV ingestion)
- Trade                           (individual trade records)
- Position                        (enriched with live quotes)
- PortfolioSnapshot               (point-in-time portfolio state)
- Journal*                        (daily journal persistence)
- PersistentContext               (user strategy & constraints)
"""

from __future__ import annotations

from datetime import date, datetime
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
    shares: float
    market_value: float
    gain_loss: float = 0.0
    gain_loss_pct: float = 0.0
    basket_name: Optional[str] = None

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


# ── FIDELITY ANALYSIS MODELS ────────────────────────────────────────────────

class AssetAllocationRow(BaseModel):
    """One Fidelity asset-allocation row for a symbol and asset class."""

    symbol: str
    description: str = ""
    account: str = ""
    asset_class: str
    weight_pct: float = 0.0
    current_value: float = 0.0


class GeographicExposureRow(BaseModel):
    """One Fidelity geographic-exposure row for a symbol."""

    symbol: str
    description: str = ""
    account: str = ""
    region: str
    country: str
    weight_pct: float = 0.0
    current_value: float = 0.0


class StyleExposureRow(BaseModel):
    """One Fidelity style-box row for a symbol."""

    symbol: str
    description: str = ""
    account: str = ""
    style: str
    weight_pct: float = 0.0
    current_value: float = 0.0


class PeriodicReturnRow(BaseModel):
    """One Fidelity account-level periodic return row."""

    period_end_date: date
    return_type: str
    account: str
    one_month_pct: Optional[float] = None
    three_month_pct: Optional[float] = None
    ytd_pct: Optional[float] = None
    one_year_pct: Optional[float] = None
    three_year_pct: Optional[float] = None
    five_year_pct: Optional[float] = None
    ten_year_pct: Optional[float] = None
    life_pct: Optional[float] = None
    life_start_date: Optional[date] = None


class FidelityAnalysisBundle(BaseModel):
    """Supplemental Fidelity analysis data for a dated export folder."""

    as_of_date: date
    source_dir: str = ""
    asset_allocation: List[AssetAllocationRow] = Field(default_factory=list)
    geographic_exposure: List[GeographicExposureRow] = Field(default_factory=list)
    style_exposure: List[StyleExposureRow] = Field(default_factory=list)
    periodic_returns: List[PeriodicReturnRow] = Field(default_factory=list)


# ── TRADE MODELS ─────────────────────────────────────────────────────────────

class Trade(BaseModel):
    """A single logged trade with metadata and rationale."""

    id: str = ""
    ticker: str
    action: str  # "BUY" or "SELL"
    shares: float
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
    shares: float
    avg_cost_basis: float
    current_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    weight_pct: float = 0.0
    day_change: float = 0.0
    day_change_pct: float = 0.0
    basket_name: Optional[str] = None

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
    fidelity_analysis: Optional[FidelityAnalysisBundle] = None

    def model_post_init(self, __context) -> None:
        if not self.invested_value:
            self.invested_value = sum(p.market_value for p in self.positions)


class JournalSnapshotSummary(BaseModel):
    """Summary of the latest snapshot attached to a journal entry."""

    snapshot_path: str = ""
    snapshot_timestamp: datetime
    total_value: float
    cash: float = 0.0
    invested_value: float = 0.0
    today_change: float = 0.0
    today_change_pct: float = 0.0
    total_gain_loss: float = 0.0
    total_gain_loss_pct: float = 0.0
    cumulative_return_pct: float = 0.0
    positions_count: int = 0


class JournalPnlSummary(BaseModel):
    """Daily P&L and risk summary stored in the journal."""

    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    total_gain_loss: float = 0.0
    total_gain_loss_pct: float = 0.0
    cumulative_return_pct: float = 0.0
    today_change: float = 0.0
    today_change_pct: float = 0.0
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    concentration_top3_pct: float = 0.0


class JournalExposureSummary(BaseModel):
    """Compact Fidelity exposure context stored in the daily journal."""

    source_dir: str = ""
    top_asset_classes: List[str] = Field(default_factory=list)
    top_regions: List[str] = Field(default_factory=list)
    top_countries: List[str] = Field(default_factory=list)
    top_styles: List[str] = Field(default_factory=list)
    fidelity_period_end: Optional[date] = None
    fidelity_twr_ytd_pct: Optional[float] = None
    fidelity_twr_life_pct: Optional[float] = None


class JournalPromptRecord(BaseModel):
    """Metadata for a generated prompt that is attached to the daily journal."""

    created_at: datetime = Field(default_factory=datetime.now)
    prompt_type: str = "trade"
    question: str = ""
    output_path: str = ""
    snapshot_path: str = ""
    token_count: int = 0


class JournalDecisionRecord(BaseModel):
    """Saved LLM response or decision summary attached to the daily journal."""

    recorded_at: datetime = Field(default_factory=datetime.now)
    prompt_output_path: str = ""
    summary: str = ""
    response_text: str = ""


class JournalEntry(BaseModel):
    """One daily journal entry that accumulates portfolio context and actions."""

    entry_date: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    snapshot: Optional[JournalSnapshotSummary] = None
    pnl_summary: Optional[JournalPnlSummary] = None
    exposure_summary: Optional[JournalExposureSummary] = None
    trades: List[Trade] = Field(default_factory=list)
    prompts: List[JournalPromptRecord] = Field(default_factory=list)
    decisions: List[JournalDecisionRecord] = Field(default_factory=list)


# ── PERSISTENT CONTEXT MODEL ─────────────────────────────────────────────────

class OptionsGating(BaseModel):
    """Whether option recommendations may be presented as executable.

    The main cash account's Level-2 privilege is pending and an option order needs
    the position/account >= ``options_min_account_value``. When gating fails, option
    ideas are shown but labeled "advisory only — not executable" (label, don't hide).
    """

    options_enabled: bool = False
    options_min_account_value: float = 10000.0
    options_account_label: str = "main account (Level-2 application pending)"
    enabled_accounts: List[str] = Field(default_factory=list)


class PersistentContext(BaseModel):
    """User-defined investment strategy and constraints loaded from YAML."""

    investment_strategy: str = ""
    risk_tolerance: str = "Moderate"
    investment_horizon: str = "6 months"
    constraints: List[str] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)
    benchmark: str = "SPY"
    notes: str = ""
    options_gating: OptionsGating = Field(default_factory=OptionsGating)


# ── PERFORMANCE METRICS MODEL ────────────────────────────────────────────────

class PerformanceMetrics(BaseModel):
    """Computed performance metrics for the portfolio."""

    cumulative_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    annualized_volatility_pct: float = 0.0
    daily_returns: List[float] = Field(default_factory=list)
    sharpe_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    max_drawdown_start: Optional[datetime] = None
    max_drawdown_end: Optional[datetime] = None
    max_drawdown_peak_value: float = 0.0
    max_drawdown_trough_value: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    concentration_top3_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
