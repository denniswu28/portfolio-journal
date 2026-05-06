"""
csv_loader.py - Load portfolio data from either a simple CSV or a Fidelity export.

Supported formats:
    1. Simple CSV:
       ticker,shares,cost_basis,current_price[,company_name]
    2. Fidelity positions export:
       symbol,quantity,last price,current value,description,average cost basis,
       cost basis,today's gain/loss,total gain/loss,...
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .models import RawPortfolioData, RawPosition


LEGACY_REQUIRED_COLUMNS = {"ticker", "shares", "costbasis", "currentprice"}
FIDELITY_REQUIRED_COLUMN_GROUPS = (
    {"symbol", "ticker"},
    {"quantity", "shares"},
    {"lastprice", "currentprice", "currentvalue", "marketvalue"},
    {"averagecostbasis", "costbasis"},
)

FIELD_ALIASES = {
    "ticker": {"ticker", "symbol"},
    "shares": {"shares", "quantity"},
    "current_price": {"currentprice", "lastprice", "price"},
    "market_value": {"marketvalue", "currentvalue", "value"},
    "company_name": {"companyname", "description", "securitydescription", "name"},
    "cost_basis_total": {
        "costbasis",
        "totalcostbasis",
        "costbasistotal",
        "bookcost",
    },
    "cost_basis_per_share": {
        "costbasispershare",
        "averagecostbasis",
        "averagecost",
        "avgcostbasis",
        "avgcost",
    },
    "day_change": {
        "daychange",
        "todaysgainloss",
        "todaysgainlossdollar",
        "todaysgainlossdollars",
        "todaygainloss",
    },
    "day_change_pct": {
        "daychangepct",
        "daychangepercent",
        "todaysgainlosspct",
        "todaysgainlosspercent",
        "todaysgainlosspercentage",
        "todaygainlosspct",
        "todaygainlosspercent",
    },
    "gain_loss": {
        "gainloss",
        "totalgainloss",
        "totalgainlossdollar",
        "totalgainlossdollars",
    },
    "gain_loss_pct": {
        "gainlosspct",
        "gainlosspercent",
        "gainlosspercentage",
        "totalgainlosspct",
        "totalgainlosspercent",
        "totalgainlosspercentage",
    },
    "position_type": {"type", "securitytype", "assettype"},
}


class CSVLoader:
    """Load portfolio data from a supported CSV export."""

    def load(
        self,
        filepath: str | Path,
        cash: float = 0.0,
        total_value: Optional[float] = None,
    ) -> RawPortfolioData:
        """
        Load portfolio from a CSV file.

        Args:
            filepath: Path to the CSV file.
            cash: Cash balance override or supplement.
            total_value: Override total portfolio value. If None, it is computed
                         as sum(position market values) + cash.

        Returns:
            RawPortfolioData with positions derived from the CSV rows.

        Raises:
            ValueError: If the CSV does not match a supported schema.
            FileNotFoundError: If the file does not exist.
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {filepath}")

        source_text = path.read_text(encoding="utf-8-sig", errors="ignore")
        parsed_at = _extract_fidelity_downloaded_at(source_text)
        df = pd.read_csv(path, dtype=str, index_col=False)
        normalized_columns = {_normalize_column_name(c): c for c in df.columns}

        if LEGACY_REQUIRED_COLUMNS.issubset(normalized_columns):
            return self._load_simple_csv(
                df=df,
                normalized_columns=normalized_columns,
                cash=cash,
                total_value=total_value,
                parsed_at=parsed_at,
            )

        if {"ticker", "shares", "currentprice"}.issubset(normalized_columns):
            missing = LEGACY_REQUIRED_COLUMNS - set(normalized_columns)
            raise ValueError(
                f"CSV is missing required columns: {missing}. "
                "Required: {'ticker', 'shares', 'cost_basis', 'current_price'}"
            )

        if _matches_fidelity_schema(normalized_columns):
            return self._load_fidelity_csv(
                df=df,
                normalized_columns=normalized_columns,
                cash=cash,
                total_value=total_value,
                parsed_at=parsed_at,
            )

        raise ValueError(
            "CSV format not recognized. Provide either a simple portfolio CSV "
            "(ticker, shares, cost_basis, current_price) or a Fidelity positions export."
        )

    def _load_simple_csv(
        self,
        df: pd.DataFrame,
        normalized_columns: dict[str, str],
        cash: float,
        total_value: Optional[float],
        parsed_at: Optional[datetime] = None,
    ) -> RawPortfolioData:
        positions: list[RawPosition] = []

        for _, row in df.iterrows():
            ticker = _clean_text(row[normalized_columns["ticker"]]).upper()
            if not ticker:
                continue

            shares = _parse_number(row[normalized_columns["shares"]])
            cost_basis = _parse_number(row[normalized_columns["costbasis"]])
            current_price = _parse_number(row[normalized_columns["currentprice"]])
            company_name_col = normalized_columns.get("companyname")
            company_name = _clean_text(row[company_name_col]) if company_name_col else ticker
            if not company_name:
                company_name = ticker

            market_value = shares * current_price
            gain_loss = (current_price - cost_basis) * shares
            gain_loss_pct = (((current_price / cost_basis) - 1) * 100) if cost_basis else 0.0

            positions.append(
                RawPosition(
                    ticker=ticker,
                    company_name=company_name,
                    current_price=current_price,
                    cost_basis_per_share=cost_basis,
                    shares=shares,
                    market_value=market_value,
                    gain_loss=gain_loss,
                    gain_loss_pct=gain_loss_pct,
                )
            )

        positions = _aggregate_positions(positions)
        computed_total = total_value if total_value is not None else sum(p.market_value for p in positions) + cash

        return RawPortfolioData(
            total_value=computed_total,
            cash=cash,
            total_gain_loss=sum(p.gain_loss for p in positions),
            total_gain_loss_pct=_portfolio_gain_loss_pct(positions),
            positions=positions,
            parsed_at=parsed_at or datetime.now(),
        )

    def _load_fidelity_csv(
        self,
        df: pd.DataFrame,
        normalized_columns: dict[str, str],
        cash: float,
        total_value: Optional[float],
        parsed_at: Optional[datetime] = None,
    ) -> RawPortfolioData:
        positions: list[RawPosition] = []
        derived_cash = 0.0
        today_change_total = 0.0

        for _, row in df.iterrows():
            ticker = _get_text_field(row, normalized_columns, "ticker").upper()
            company_name = _get_text_field(row, normalized_columns, "company_name")
            shares = _get_numeric_field(row, normalized_columns, "shares")
            market_value = _get_numeric_field(row, normalized_columns, "market_value")
            current_price = _get_numeric_field(row, normalized_columns, "current_price")
            cost_basis_per_share = _get_numeric_field(row, normalized_columns, "cost_basis_per_share")
            total_cost_basis = _get_numeric_field(row, normalized_columns, "cost_basis_total")
            day_change = _get_numeric_field(row, normalized_columns, "day_change")
            day_change_pct = _get_numeric_field(row, normalized_columns, "day_change_pct")
            gain_loss = _get_numeric_field(row, normalized_columns, "gain_loss")
            gain_loss_pct = _get_numeric_field(row, normalized_columns, "gain_loss_pct")
            position_type = _get_text_field(row, normalized_columns, "position_type")

            if not ticker and not company_name:
                continue

            if _is_cash_row(
                ticker=ticker,
                company_name=company_name,
                position_type=position_type,
                shares=shares,
                market_value=market_value,
            ):
                derived_cash += market_value
                today_change_total += day_change
                continue

            if shares <= 0:
                continue

            if current_price == 0.0 and market_value:
                current_price = market_value / shares
            if market_value == 0.0 and current_price:
                market_value = shares * current_price
            if cost_basis_per_share == 0.0 and total_cost_basis:
                cost_basis_per_share = total_cost_basis / shares
            if total_cost_basis == 0.0 and cost_basis_per_share:
                total_cost_basis = cost_basis_per_share * shares
            if gain_loss == 0.0 and market_value and total_cost_basis:
                gain_loss = market_value - total_cost_basis
            if gain_loss_pct == 0.0 and total_cost_basis:
                gain_loss_pct = (gain_loss / total_cost_basis) * 100

            if not ticker or current_price <= 0.0 or cost_basis_per_share < 0.0:
                continue

            positions.append(
                RawPosition(
                    ticker=ticker,
                    company_name=company_name or ticker,
                    current_price=current_price,
                    day_change=day_change,
                    day_change_pct=day_change_pct,
                    cost_basis_per_share=cost_basis_per_share,
                    shares=shares,
                    market_value=market_value,
                    gain_loss=gain_loss,
                    gain_loss_pct=gain_loss_pct,
                )
            )
            today_change_total += day_change

        positions = _aggregate_positions(positions)
        computed_cash = cash + derived_cash
        invested_value = sum(p.market_value for p in positions)
        computed_total = total_value if total_value is not None else invested_value + computed_cash
        total_gain_loss = sum(p.gain_loss for p in positions)
        cost_basis_total = sum(p.cost_basis_per_share * p.shares for p in positions)
        total_gain_loss_pct = (total_gain_loss / cost_basis_total * 100) if cost_basis_total else 0.0
        today_change_pct = (today_change_total / (computed_total - today_change_total) * 100) if computed_total != today_change_total else 0.0

        return RawPortfolioData(
            total_value=computed_total,
            cash=computed_cash,
            today_change=today_change_total,
            today_change_pct=today_change_pct,
            total_gain_loss=total_gain_loss,
            total_gain_loss_pct=total_gain_loss_pct,
            positions=positions,
            parsed_at=parsed_at or datetime.now(),
        )


def _matches_fidelity_schema(normalized_columns: dict[str, str]) -> bool:
    available = set(normalized_columns)
    return all(any(option in available for option in group) for group in FIDELITY_REQUIRED_COLUMN_GROUPS)


def _aggregate_positions(positions: list[RawPosition]) -> list[RawPosition]:
    grouped: dict[str, RawPosition] = {}

    for position in positions:
        existing = grouped.get(position.ticker)
        if not existing:
            grouped[position.ticker] = position
            continue

        total_shares = existing.shares + position.shares
        total_cost_basis = (
            (existing.market_value - existing.gain_loss)
            + (position.market_value - position.gain_loss)
        )
        total_market_value = existing.market_value + position.market_value
        total_gain_loss = existing.gain_loss + position.gain_loss
        total_day_change = existing.day_change + position.day_change

        grouped[position.ticker] = RawPosition(
            ticker=position.ticker,
            company_name=existing.company_name or position.company_name,
            current_price=(total_market_value / total_shares) if total_shares else 0.0,
            day_change=total_day_change,
            day_change_pct=(total_day_change / (total_market_value - total_day_change) * 100)
            if total_market_value != total_day_change
            else 0.0,
            cost_basis_per_share=(total_cost_basis / total_shares) if total_shares else 0.0,
            shares=total_shares,
            market_value=total_market_value,
            gain_loss=total_gain_loss,
            gain_loss_pct=(total_gain_loss / total_cost_basis * 100) if total_cost_basis else 0.0,
        )

    return sorted(grouped.values(), key=lambda p: p.ticker)


def _portfolio_gain_loss_pct(positions: list[RawPosition]) -> float:
    total_cost_basis = sum(p.cost_basis_per_share * p.shares for p in positions)
    total_gain_loss = sum(p.gain_loss for p in positions)
    return (total_gain_loss / total_cost_basis * 100) if total_cost_basis else 0.0


def _get_text_field(row: pd.Series, normalized_columns: dict[str, str], field: str) -> str:
    for alias in FIELD_ALIASES[field]:
        column = normalized_columns.get(alias)
        if column is None:
            continue
        text = _clean_text(row[column])
        if text:
            return text
    return ""


def _get_numeric_field(row: pd.Series, normalized_columns: dict[str, str], field: str) -> float:
    for alias in FIELD_ALIASES[field]:
        column = normalized_columns.get(alias)
        if column is None:
            continue
        value = _parse_number(row[column])
        if value != 0.0 or _clean_text(row[column]) in {"0", "0.0", "$0.00", "0.00%"}:
            return value
    return 0.0


def _is_cash_row(
    ticker: str,
    company_name: str,
    position_type: str,
    shares: float,
    market_value: float,
) -> bool:
    description = company_name.lower()
    return (
        market_value > 0.0
        and shares == 0.0
        and (
            ticker.endswith("**")
            or "money market" in description
            or ("cash" in position_type.lower() and not ticker)
        )
    )


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def _parse_number(value: object) -> float:
    text = _clean_text(value)
    if not text or text == "########":
        return 0.0

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = text.replace("$", "").replace("%", "").replace(",", "").strip()
    text = re.sub(r"\s+", "", text)
    if not text:
        return 0.0

    try:
        number = float(text)
    except ValueError:
        return 0.0

    if math.isnan(number):
        return 0.0
    return -number if negative else number


def _extract_fidelity_downloaded_at(source_text: str) -> Optional[datetime]:
    """Parse Fidelity's footer download timestamp when it is present."""
    match = re.search(
        r"Date\s+downloaded\s+([A-Za-z]{3})-(\d{1,2})-(\d{4})\s+"
        r"(\d{1,2}):(\d{2})\s+([ap])\.?m\.?\s+ET",
        source_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    month_text, day_text, year_text, hour_text, minute_text, meridiem = match.groups()
    try:
        month = datetime.strptime(month_text.title(), "%b").month
    except ValueError:
        return None

    hour = int(hour_text)
    if meridiem.lower() == "p" and hour != 12:
        hour += 12
    elif meridiem.lower() == "a" and hour == 12:
        hour = 0

    return datetime(
        year=int(year_text),
        month=month,
        day=int(day_text),
        hour=hour,
        minute=int(minute_text),
    )


def _normalize_column_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())
