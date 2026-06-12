"""Tests for Fidelity export bundle organization."""

import json
from datetime import date

from src.data_ingestion.fidelity_bundle import classify_export, organize_fidelity_exports


def test_classify_supported_exports(tmp_path):
    positions = tmp_path / "Portfolio_Positions.csv"
    positions.write_text(
        "Account Number,Account Name,Basket Name,Symbol,Description,Quantity,Last Price,Current Value,Average Cost Basis\n",
        encoding="utf-8",
    )
    allocation = tmp_path / "Asset_allocation.csv"
    allocation.write_text(
        "Symbol,Description,Account,Asset class,Weight,Current value\n",
        encoding="utf-8",
    )
    periodic = tmp_path / "Periodic_returns.csv"
    periodic.write_text(
        '"Prior month end performance as of Apr-30-2026"\n',
        encoding="utf-8",
    )

    assert classify_export(positions) == "positions"
    assert classify_export(allocation) == "asset_allocation"
    assert classify_export(periodic) == "periodic_returns"


def test_organize_fidelity_exports_writes_manifest(tmp_path):
    source = tmp_path / "Asset_allocation.csv"
    source.write_text(
        "Symbol,Description,Account,Asset class,Weight,Current value\n",
        encoding="utf-8",
    )
    snapshots_dir = tmp_path / "portfolio_snapshots"

    organized = organize_fidelity_exports(
        [source],
        snapshots_dir=snapshots_dir,
        bundle_date=date(2026, 5, 6),
    )

    target_dir = snapshots_dir / "2026-05-06"
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))

    assert len(organized) == 1
    assert (target_dir / "asset_allocation.csv").exists()
    assert source.exists()
    assert manifest["bundle_date"] == "2026-05-06"
    assert manifest["files"][0]["file_type"] == "asset_allocation"