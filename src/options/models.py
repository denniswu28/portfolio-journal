"""
models.py - Option leg / strategy data models for the deterministic harness.

These models hold only primitives (ticker, right, strike, expiry, ...) so they
serialize cleanly to JSON for logging and journaling. QuantLib objects are never
stored here; they are constructed on demand in ``pricing.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


CALL = "CALL"
PUT = "PUT"
BUY = "BUY"
SELL = "SELL"

# How a short leg's risk is secured (governs Level-2 compliance).
SECURED_CASH = "cash"          # cash-secured put
SECURED_STOCK = "stock"        # covered call (long stock)
SECURED_SHORT_STOCK = "short_stock"  # covered put (short stock)


class OptionLeg(BaseModel):
    """A single option leg within a strategy."""

    underlying: str
    right: str            # CALL | PUT
    strike: float
    expiry: date
    action: str           # BUY | SELL
    contracts: int = 1
    multiplier: int = 100
    entry_price: Optional[float] = None  # premium per share at entry

    @field_validator("underlying")
    @classmethod
    def _upper_underlying(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("right")
    @classmethod
    def _valid_right(cls, v: str) -> str:
        token = v.upper().strip()
        if token in ("C", "CALL"):
            return CALL
        if token in ("P", "PUT"):
            return PUT
        raise ValueError(f"right must be CALL or PUT, got '{v}'")

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        token = v.upper().strip()
        if token in ("BUY", "LONG", "B"):
            return BUY
        if token in ("SELL", "SHORT", "S", "WRITE"):
            return SELL
        raise ValueError(f"action must be BUY or SELL, got '{v}'")

    @property
    def sign(self) -> int:
        """+1 for long (BUY), -1 for short (SELL)."""
        return 1 if self.action == BUY else -1

    @property
    def quantity(self) -> int:
        """Signed share-equivalent quantity (contracts * multiplier * sign)."""
        return self.sign * self.contracts * self.multiplier

    def is_call(self) -> bool:
        return self.right == CALL

    def is_put(self) -> bool:
        return self.right == PUT


class OptionStrategy(BaseModel):
    """A named multi-leg option strategy on one underlying."""

    name: str
    underlying: str
    legs: List[OptionLeg] = Field(default_factory=list)
    secured_by: Optional[str] = None      # SECURED_CASH | SECURED_STOCK | SECURED_SHORT_STOCK
    underlying_shares: float = 0.0        # held shares backing covered structures
    opened_at: datetime = Field(default_factory=datetime.now)
    notes: str = ""

    @field_validator("underlying")
    @classmethod
    def _upper_underlying(cls, v: str) -> str:
        return v.upper().strip()

    @property
    def expiries(self) -> List[date]:
        return sorted({leg.expiry for leg in self.legs})

    @property
    def is_defined_risk(self) -> bool:
        """True when no Level-2 violations are present."""
        from src.options.strategies import validate_level2  # late import to avoid cycle

        return not validate_level2(self)


class OptionPosition(BaseModel):
    """An open (or closed) option strategy with entry data and exit rules."""

    id: str = ""
    strategy: OptionStrategy
    entry_net_debit: float = 0.0      # position dollars: + debit paid, - credit received
    opened_at: datetime = Field(default_factory=datetime.now)
    status: str = "OPEN"              # OPEN | CLOSED
    take_profit_pct: Optional[float] = None   # of risk base (debit or credit)
    stop_loss_pct: Optional[float] = None
    close_by_dte: Optional[int] = 21
    rationale: str = ""
    tags: List[str] = Field(default_factory=list)
    closed_at: Optional[datetime] = None
    exit_net_debit: Optional[float] = None

    @property
    def underlying(self) -> str:
        return self.strategy.underlying

    @property
    def is_credit(self) -> bool:
        return self.entry_net_debit < 0

    @property
    def risk_base(self) -> float:
        """Absolute entry premium used as the percentage base for TP/SL."""
        return abs(self.entry_net_debit)

    def model_post_init(self, __context) -> None:
        if not self.id:
            stamp = self.opened_at.strftime("%Y%m%d%H%M%S")
            self.id = f"{self.strategy.underlying}_{self.strategy.name.replace(' ', '-')}_{stamp}"


class OptionTrade(BaseModel):
    """A logged option trade event (open / close / roll)."""

    id: str = ""
    position_id: str = ""
    underlying: str
    structure: str
    action: str                       # OPEN | CLOSE | ROLL
    net_debit: float                  # + debit paid, - credit received (position dollars)
    contracts: int = 1
    timestamp: datetime = Field(default_factory=datetime.now)
    rationale: str = ""
    tags: List[str] = Field(default_factory=list)

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        token = v.upper().strip()
        if token not in ("OPEN", "CLOSE", "ROLL"):
            raise ValueError(f"action must be OPEN, CLOSE, or ROLL, got '{v}'")
        return token

    def model_post_init(self, __context) -> None:
        if not self.id:
            self.id = f"{self.underlying}_{self.action}_{self.timestamp.strftime('%Y%m%d%H%M%S')}"
