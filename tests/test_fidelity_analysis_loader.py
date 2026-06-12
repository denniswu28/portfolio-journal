"""Tests for Fidelity supplemental analysis CSV loaders."""

from datetime import date

import pytest

from src.data_ingestion.fidelity_analysis_loader import (
    load_analysis_bundle,
    load_asset_allocation,
    load_geographic_exposure,
    load_periodic_returns,
    load_style_exposure,
)


def test_load_asset_allocation_strips_disclosure(tmp_path):
    path = tmp_path / "asset_allocation.csv"
    path.write_text(
        '"Symbol","Description","Account","Asset class","Weight","Current value",\n'
        '"AAPL","Apple Inc","Z1","Domestic Stock","90.00%","$900.00",\n'
        '"AAPL","Apple Inc","Z1","Short Term","10.00%","$100.00",\n'
        '"Balance and Returns",\n'
        '"Disclosure text",\n',
        encoding="utf-8",
    )

    rows = load_asset_allocation(path)

    assert len(rows) == 2
    assert rows[0].asset_class == "Domestic Stock"
    assert rows[0].current_value == pytest.approx(900.0)


def test_load_geographic_and_style_exposure(tmp_path):
    geo_path = tmp_path / "geographic_exposure.csv"
    geo_path.write_text(
        '"Symbol","Description","Account","Region","Country","Weight","Current value"\n'
        '"TSM","Taiwan Semiconductor","Z1","Asia Developed","Taiwan","100.00%","$418.25"\n',
        encoding="utf-8",
    )
    style_path = tmp_path / "style.csv"
    style_path.write_text(
        '"Symbol","Description","Account","Style","Weight","Current value"\n'
        '"TSM","Taiwan Semiconductor","Z1","Large Growth","100.00%","$418.22"\n',
        encoding="utf-8",
    )

    geo_rows = load_geographic_exposure(geo_path)
    style_rows = load_style_exposure(style_path)

    assert geo_rows[0].region == "Asia Developed"
    assert geo_rows[0].country == "Taiwan"
    assert style_rows[0].style == "Large Growth"


def test_load_periodic_returns_parses_period_end_and_returns(tmp_path):
    path = tmp_path / "periodic_returns.csv"
    path.write_text(
        '"Prior month end performance as of Apr-30-2026"\n'
        '""\n'
        '"Annualized Returns"\n'
        '""\n'
        '"Time-weighted rate of return (pre-tax)","1 Month","3 Month","YTD","1 Year","3 Year","5 Year","10 Year","Life of available data","Life start date"\n'
        '"Cash Management Z1","+10.46%","+4.97%","+9.07%","+11.58%","--","--","--","+7.75%","Dec-04-2024"\n'
        '""\n'
        '"Money-weighted rate of return (pre-tax)","1 Month","3 Month","YTD","1 Year","3 Year","5 Year","10 Year","Life of available data","Life start date"\n'
        '"Cash Management Z1","+10.49%","+7.65%","+13.20%","+17.56%","--","--","--","+14.64%","Dec-04-2024"\n'
        '"Important information about performance returns."\n',
        encoding="utf-8",
    )

    rows = load_periodic_returns(path)

    assert len(rows) == 2
    assert rows[0].period_end_date == date(2026, 4, 30)
    assert rows[0].return_type == "time_weighted"
    assert rows[0].ytd_pct == pytest.approx(9.07)
    assert rows[0].life_start_date == date(2024, 12, 4)


def test_load_analysis_bundle_uses_folder_date(tmp_path):
    folder = tmp_path / "2026-05-06"
    folder.mkdir()
    (folder / "asset_allocation.csv").write_text(
        '"Symbol","Description","Account","Asset class","Weight","Current value"\n'
        '"AAPL","Apple Inc","Z1","Domestic Stock","100.00%","$100.00"\n',
        encoding="utf-8",
    )

    bundle = load_analysis_bundle(folder)

    assert bundle.as_of_date == date(2026, 5, 6)
    assert len(bundle.asset_allocation) == 1