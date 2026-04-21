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
