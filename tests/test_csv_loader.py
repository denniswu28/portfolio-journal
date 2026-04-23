"""Tests for csv_loader.py"""

import pytest
import tempfile
import os

from src.data_ingestion.csv_loader import CSVLoader


CSV_CONTENT = """ticker,shares,cost_basis,current_price
BABA,500,114.52,135.38
DAL,300,49.08,70.22
LUV,300,31.15,40.92
NVDA,15,124.94,199.88
UAL,300,58.70,97.13
"""

CSV_WITH_COMPANY = """ticker,shares,cost_basis,current_price,company_name
AAPL,100,150.00,175.00,Apple Inc.
GOOG,10,2000.00,2500.00,Alphabet Inc.
"""

FIDELITY_CSV = """Account Number,Account Name,Basket Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percentage,Total Gain/Loss Dollar,Total Gain/Loss Percentage,Percent Of Account,Cost Basis,Average Cost Basis,Type
Z12345678,Individual - TOD,,SPAXX**,HELD IN MONEY MARKET,,1.00,0.00,1250.55,0.00,0.00%,0.00,0.00%,10.00%,1250.55,1.00,Cash
Z12345678,Individual - TOD,Tech,AAPL,APPLE INC,2.543,$273.17,$7.00,$694.67,$17.80,2.62%,$23.62,3.52%,4.96%,$671.05,$263.88,Equity
Z12345678,Individual - TOD,AI Future,SMH,VANECK ETF,0.737,$476.83,$12.17,$351.42,$8.96,2.61%,$56.35,19.09%,2.51%,$295.07,$400.37,ETF
Z12345678,Individual - TOD,Income,SMH,VANECK ETF,0.327,$476.83,$12.17,$155.92,$3.97,2.61%,$24.53,18.67%,1.11%,$131.39,$401.80,ETF
Brokerage services are provided by Fidelity Brokerage Services LLC (FBS),900 Salem Street,,,,,,,,,,,,,,,
Date downloaded Apr-22-2026 9:19 p.m ET,,,,,,,,,,,,,,,
"""

FIDELITY_CSV_WITH_TRAILING_EMPTY_FIELD = """Account Number,Account Name,Basket Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type
Z25225580,Individual - TOD,,SPAXX**,HELD IN MONEY MARKET,,,,$2069.43,,,,,100.00%,,,Cash,
Z26679636,Cash Management (Individual - TOD),Tech,AAPL,APPLE INC,2.543,$273.17,+$7.00,$694.67,+$17.80,+2.62%,+$23.62,+3.52%,4.96%,$671.05,$263.88,Cash,
Z26679636,Cash Management (Individual - TOD),Tech,GOOG,ALPHABET INC CAP STK CL C,2.016,$337.73,+$7.26,$680.86,+$14.63,+2.19%,+$34.74,+5.37%,4.86%,$646.12,$320.50,Cash,

"The data and information in this spreadsheet is provided to you solely for your use and is not for distribution."
"""

CSV_MISSING_COLUMN = """ticker,shares,current_price
AAPL,100,175.00
"""


class TestCSVLoader:
    def setup_method(self):
        self.loader = CSVLoader()

    def _write_temp_csv(self, content):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path

    def test_load_basic_csv(self):
        path = self._write_temp_csv(CSV_CONTENT)
        try:
            result = self.loader.load(path)
            assert len(result.positions) == 5
        finally:
            os.unlink(path)

    def test_load_baba_position(self):
        path = self._write_temp_csv(CSV_CONTENT)
        try:
            result = self.loader.load(path)
            baba = next(p for p in result.positions if p.ticker == "BABA")
            assert baba.shares == 500
            assert baba.cost_basis_per_share == pytest.approx(114.52)
            assert baba.current_price == pytest.approx(135.38)
        finally:
            os.unlink(path)

    def test_market_value_computed(self):
        path = self._write_temp_csv(CSV_CONTENT)
        try:
            result = self.loader.load(path)
            baba = next(p for p in result.positions if p.ticker == "BABA")
            assert baba.market_value == pytest.approx(500 * 135.38)
        finally:
            os.unlink(path)

    def test_gain_loss_computed(self):
        path = self._write_temp_csv(CSV_CONTENT)
        try:
            result = self.loader.load(path)
            baba = next(p for p in result.positions if p.ticker == "BABA")
            expected_gl = (135.38 - 114.52) * 500
            assert baba.gain_loss == pytest.approx(expected_gl, rel=1e-3)
        finally:
            os.unlink(path)

    def test_load_with_company_name(self):
        path = self._write_temp_csv(CSV_WITH_COMPANY)
        try:
            result = self.loader.load(path)
            aapl = next(p for p in result.positions if p.ticker == "AAPL")
            assert aapl.company_name == "Apple Inc."
        finally:
            os.unlink(path)

    def test_missing_required_column_raises(self):
        path = self._write_temp_csv(CSV_MISSING_COLUMN)
        try:
            with pytest.raises(ValueError, match="missing required columns"):
                self.loader.load(path)
        finally:
            os.unlink(path)

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            self.loader.load("/nonexistent/path/file.csv")

    def test_cash_parameter_used(self):
        path = self._write_temp_csv(CSV_CONTENT)
        try:
            result = self.loader.load(path, cash=5000.0)
            assert result.cash == pytest.approx(5000.0)
        finally:
            os.unlink(path)

    def test_total_value_computed(self):
        path = self._write_temp_csv(CSV_CONTENT)
        try:
            result = self.loader.load(path, cash=1000.0)
            expected = sum(p.market_value for p in result.positions) + 1000.0
            assert result.total_value == pytest.approx(expected)
        finally:
            os.unlink(path)

    def test_loads_fidelity_positions_export(self):
        path = self._write_temp_csv(FIDELITY_CSV)
        try:
            result = self.loader.load(path)
            assert len(result.positions) == 2
            assert result.cash == pytest.approx(1250.55)
            assert result.total_value == pytest.approx(
                1250.55 + sum(position.market_value for position in result.positions)
            )
        finally:
            os.unlink(path)

    def test_fidelity_loader_preserves_fractional_shares(self):
        path = self._write_temp_csv(FIDELITY_CSV)
        try:
            result = self.loader.load(path)
            aapl = next(p for p in result.positions if p.ticker == "AAPL")
            assert aapl.shares == pytest.approx(2.543)
            assert aapl.cost_basis_per_share == pytest.approx(263.88)
            assert aapl.current_price == pytest.approx(273.17)
        finally:
            os.unlink(path)

    def test_fidelity_loader_aggregates_duplicate_symbols(self):
        path = self._write_temp_csv(FIDELITY_CSV)
        try:
            result = self.loader.load(path)
            smh = next(p for p in result.positions if p.ticker == "SMH")
            assert smh.shares == pytest.approx(1.064)
            assert smh.market_value == pytest.approx(351.42 + 155.92)
            assert smh.cost_basis_per_share == pytest.approx(
                (295.07 + 131.39) / 1.064
            )
        finally:
            os.unlink(path)

    def test_fidelity_loader_handles_trailing_empty_field_rows(self):
        path = self._write_temp_csv(FIDELITY_CSV_WITH_TRAILING_EMPTY_FIELD)
        try:
            result = self.loader.load(path)
            tickers = [position.ticker for position in result.positions]

            assert tickers == ["AAPL", "GOOG"]
            assert result.cash == pytest.approx(2069.43)

            aapl = next(p for p in result.positions if p.ticker == "AAPL")
            assert aapl.company_name == "APPLE INC"
            assert aapl.shares == pytest.approx(2.543)
            assert aapl.current_price == pytest.approx(273.17)
        finally:
            os.unlink(path)
