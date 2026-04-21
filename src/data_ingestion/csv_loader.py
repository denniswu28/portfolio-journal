"""
csv_loader.py - Load portfolio data from a CSV file (fallback ingestion method).

CSV format:
    ticker,shares,cost_basis,current_price[,company_name]

Example:
    ticker,shares,cost_basis,current_price
    BABA,500,114.52,135.38
    DAL,300,49.08,70.22
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .models import RawPortfolioData, RawPosition


REQUIRED_COLUMNS = {"ticker", "shares", "cost_basis", "current_price"}


class CSVLoader:
    """
    Loads portfolio data from a CSV file.

    The CSV must contain at minimum: ticker, shares, cost_basis, current_price.
    An optional company_name column may be included; if absent, the ticker is
    used as a placeholder.
    """

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
            cash: Cash balance (cannot be derived from CSV alone; defaults to 0).
            total_value: Override total portfolio value. If None, it is computed
                         as sum(market_values) + cash.

        Returns:
            RawPortfolioData with positions derived from the CSV rows.

        Raises:
            ValueError: If required columns are missing.
            FileNotFoundError: If the file does not exist.
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {filepath}")

        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]

        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {missing}. "
                f"Required: {REQUIRED_COLUMNS}"
            )

        positions = []
        for _, row in df.iterrows():
            ticker = str(row["ticker"]).upper().strip()
            shares = int(row["shares"])
            cost_basis = float(row["cost_basis"])
            current_price = float(row["current_price"])
            company_name = str(row.get("company_name", ticker))
            market_value = shares * current_price
            gain_loss = (current_price - cost_basis) * shares
            gain_loss_pct = (
                ((current_price / cost_basis) - 1) * 100 if cost_basis else 0.0
            )

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

        total_invested = sum(p.market_value for p in positions)
        computed_total = total_value if total_value is not None else total_invested + cash

        return RawPortfolioData(
            total_value=computed_total,
            cash=cash,
            positions=positions,
        )
