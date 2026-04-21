"""
paste_parser.py - Parse raw copy-pasted text from Investopedia Simulator portfolio page.

Usage:
    parser = PasteParser()
    data = parser.parse(raw_text)

The pasted text format from Investopedia looks like:

    Total Value $133,169.20 Today's Change $0.00(0.00%) Total Gain/Loss $32,357.31(32.10%)
    BABA Alibaba Group Holding Ltd - ADR $135.38 $0.00 (0.00%) $114.52 500 $67,690.00 $10,432.50 (18.22%) Buy More Sell
    DAL Delta Air Lines, Inc. $70.22 $0.00 (0.00%) $49.08 300 $21,066.00 $6,340.71 (43.06%) Buy More Sell
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .models import RawPortfolioData, RawPosition


def _parse_dollar(value: str) -> float:
    """Convert a dollar string like '$1,234.56' or '1234.56' to a float."""
    return float(value.replace(",", "").replace("$", "").strip())


def _parse_pct(value: str) -> float:
    """Convert a percentage string like '18.22%' or '-3.50%' to a float."""
    return float(value.replace("%", "").strip())


class PasteParser:
    """
    Parses raw copy-pasted text from the Investopedia Simulator portfolio page.

    The parser handles both the portfolio header (total value, today's change,
    total gain/loss) and each individual position line.
    """

    # Header: captures total_value, today_change $, today_change %, total_gl $, total_gl %
    HEADER_PATTERN = re.compile(
        r"Total Value\s+\$?([\d,]+\.\d{2})\s+"
        r"Today'?s Change\s+[+-]?\$?([\d,]+\.\d{2})\(([^)]*)\)\s+"
        r"Total Gain/Loss\s+[+-]?\$?([\d,]+\.\d{2})\(([^)]*)\)",
        re.IGNORECASE,
    )

    # Position line pattern:
    # TICKER  Company Name  $current_price  $day_change (day_pct%)  $cost_basis  shares  $market_value  $gain_loss (gain_pct%)
    POSITION_PATTERN = re.compile(
        r"([A-Z]{1,5})\s+"              # ticker (1-5 uppercase letters)
        r"(.+?)\s+"                      # company name (non-greedy)
        r"\$?([\d,]+\.\d{2})\s+"         # current price
        r"[+-]?\$?([\d,]+\.\d{2})\s+"    # day change $
        r"\(([^)]+)\)\s+"                # day change %
        r"\$?([\d,]+\.\d{2})\s+"         # cost basis per share
        r"(\d+)\s+"                      # shares
        r"\$?([\d,]+\.\d{2})\s+"         # market value
        r"[+-]?\$?([\d,]+\.\d{2})\s+"    # gain/loss $
        r"\(([^)]+)\)",                  # gain/loss %
    )

    # Fallback: simpler position pattern when format varies slightly
    POSITION_PATTERN_SIMPLE = re.compile(
        r"^([A-Z]{1,5})\s+(.+?)\s+\$?([\d,]+\.\d{2})",
        re.MULTILINE,
    )

    def parse(self, raw_text: str) -> RawPortfolioData:
        """
        Parse the full pasted portfolio text and return a RawPortfolioData object.

        Args:
            raw_text: Raw text copied from the Investopedia Simulator portfolio page.

        Returns:
            RawPortfolioData with header info and all parsed positions.

        Raises:
            ValueError: If the header cannot be found in the text.
        """
        text = raw_text.strip()

        total_value, today_change, today_change_pct, total_gl, total_gl_pct = (
            self._parse_header(text)
        )
        positions = self._parse_positions(text)

        # Derive cash from total value minus sum of all market values
        total_invested = sum(p.market_value for p in positions)
        cash = max(0.0, total_value - total_invested)

        return RawPortfolioData(
            total_value=total_value,
            cash=cash,
            today_change=today_change,
            today_change_pct=today_change_pct,
            total_gain_loss=total_gl,
            total_gain_loss_pct=total_gl_pct,
            positions=positions,
        )

    def _parse_header(
        self, text: str
    ) -> Tuple[float, float, float, float, float]:
        """
        Extract header fields: total_value, today_change, today_change_pct,
        total_gain_loss, total_gain_loss_pct.
        """
        match = self.HEADER_PATTERN.search(text)
        if not match:
            raise ValueError(
                "Could not find portfolio header in pasted text. "
                "Expected format: 'Total Value $X Today's Change $X(X%) Total Gain/Loss $X(X%)'"
            )

        total_value = _parse_dollar(match.group(1))
        today_change = _parse_dollar(match.group(2))
        today_change_pct = _parse_pct(match.group(3))
        total_gl = _parse_dollar(match.group(4))
        total_gl_pct = _parse_pct(match.group(5))

        return total_value, today_change, today_change_pct, total_gl, total_gl_pct

    def _parse_positions(self, text: str) -> List[RawPosition]:
        """Extract all position lines from the pasted text."""
        positions: List[RawPosition] = []

        for match in self.POSITION_PATTERN.finditer(text):
            try:
                position = RawPosition(
                    ticker=match.group(1).strip(),
                    company_name=match.group(2).strip(),
                    current_price=_parse_dollar(match.group(3)),
                    day_change=_parse_dollar(match.group(4)),
                    day_change_pct=_parse_pct(match.group(5)),
                    cost_basis_per_share=_parse_dollar(match.group(6)),
                    shares=int(match.group(7)),
                    market_value=_parse_dollar(match.group(8)),
                    gain_loss=_parse_dollar(match.group(9)),
                    gain_loss_pct=_parse_pct(match.group(10)),
                )
                positions.append(position)
            except (ValueError, IndexError) as exc:
                # Skip malformed lines rather than failing the entire parse
                print(f"Warning: Could not parse position line: {exc}")
                continue

        return positions

    def parse_from_file(self, filepath: str) -> RawPortfolioData:
        """
        Read a text file containing the pasted portfolio data and parse it.

        Args:
            filepath: Path to the .txt file containing the pasted text.

        Returns:
            RawPortfolioData parsed from the file contents.
        """
        with open(filepath, "r", encoding="utf-8") as fh:
            raw_text = fh.read()
        return self.parse(raw_text)
