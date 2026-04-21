"""Tests for paste_parser.py"""

import pytest
from src.data_ingestion.paste_parser import PasteParser, _parse_dollar, _parse_pct


SAMPLE_PASTE = (
    "Total Value $133,169.20 Today's Change $0.00(0.00%) "
    "Total Gain/Loss $32,357.31(32.10%) "
    "BABA Alibaba Group Holding Ltd - ADR $135.38 $0.00 (0.00%) "
    "$114.52 500 $67,690.00 $10,432.50 (18.22%) Buy More Sell "
    "DAL Delta Air Lines, Inc. $70.22 $0.00 (0.00%) "
    "$49.08 300 $21,066.00 $6,340.71 (43.06%) Buy More Sell "
    "LUV Southwest Airlines Co $40.92 $0.00 (0.00%) "
    "$31.15 300 $12,276.00 $2,930.37 (31.35%) Buy More Sell "
    "NVDA NVIDIA Corp $199.88 $0.00 (0.00%) "
    "$124.94 15 $2,998.20 $1,124.10 (60.03%) Buy More Sell "
    "UAL United Airlines Holdings, Inc. $97.13 $0.00 (0.00%) "
    "$58.70 300 $29,139.00 $11,529.63 (65.59%) Buy More Sell"
)


class TestHelpers:
    def test_parse_dollar_with_sign(self):
        assert _parse_dollar("$1,234.56") == pytest.approx(1234.56)

    def test_parse_dollar_without_sign(self):
        assert _parse_dollar("1234.56") == pytest.approx(1234.56)

    def test_parse_dollar_with_commas(self):
        assert _parse_dollar("$133,169.20") == pytest.approx(133169.20)

    def test_parse_pct_positive(self):
        assert _parse_pct("18.22%") == pytest.approx(18.22)

    def test_parse_pct_negative(self):
        assert _parse_pct("-3.50%") == pytest.approx(-3.50)

    def test_parse_pct_zero(self):
        assert _parse_pct("0.00%") == pytest.approx(0.0)


class TestPasteParser:
    def setup_method(self):
        self.parser = PasteParser()

    def test_parse_returns_raw_portfolio_data(self):
        result = self.parser.parse(SAMPLE_PASTE)
        assert result is not None

    def test_parse_header_total_value(self):
        result = self.parser.parse(SAMPLE_PASTE)
        assert result.total_value == pytest.approx(133169.20)

    def test_parse_header_total_gain_loss(self):
        result = self.parser.parse(SAMPLE_PASTE)
        assert result.total_gain_loss == pytest.approx(32357.31)

    def test_parse_header_today_change(self):
        result = self.parser.parse(SAMPLE_PASTE)
        assert result.today_change == pytest.approx(0.0)

    def test_parse_positions_count(self):
        result = self.parser.parse(SAMPLE_PASTE)
        assert len(result.positions) == 5

    def test_parse_first_position_ticker(self):
        result = self.parser.parse(SAMPLE_PASTE)
        tickers = [p.ticker for p in result.positions]
        assert "BABA" in tickers

    def test_parse_baba_position(self):
        result = self.parser.parse(SAMPLE_PASTE)
        baba = next(p for p in result.positions if p.ticker == "BABA")
        assert baba.shares == 500
        assert baba.current_price == pytest.approx(135.38)
        assert baba.cost_basis_per_share == pytest.approx(114.52)
        assert baba.market_value == pytest.approx(67690.00)
        assert baba.gain_loss == pytest.approx(10432.50)
        assert baba.gain_loss_pct == pytest.approx(18.22)

    def test_parse_dal_position(self):
        result = self.parser.parse(SAMPLE_PASTE)
        dal = next(p for p in result.positions if p.ticker == "DAL")
        assert dal.shares == 300
        assert dal.current_price == pytest.approx(70.22)
        assert dal.cost_basis_per_share == pytest.approx(49.08)

    def test_cash_derived_correctly(self):
        result = self.parser.parse(SAMPLE_PASTE)
        # Total invested: 67690 + 21066 + 12276 + 2998.20 + 29139 = 133169.20
        # Cash should be approx 0
        assert result.cash == pytest.approx(0.0, abs=1.0)

    def test_invalid_text_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not find portfolio header"):
            self.parser.parse("This is not a valid portfolio paste")

    def test_empty_text_raises_value_error(self):
        with pytest.raises(ValueError):
            self.parser.parse("")

    def test_all_tickers_parsed(self):
        result = self.parser.parse(SAMPLE_PASTE)
        tickers = {p.ticker for p in result.positions}
        assert tickers == {"BABA", "DAL", "LUV", "NVDA", "UAL"}
