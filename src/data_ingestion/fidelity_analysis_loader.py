"""Load supplemental Fidelity analysis CSV exports."""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, TypeVar

from src.data_ingestion.csv_loader import _clean_text, _parse_number
from src.data_ingestion.models import (
    AssetAllocationRow,
    FidelityAnalysisBundle,
    GeographicExposureRow,
    PeriodicReturnRow,
    StyleExposureRow,
)


T = TypeVar("T")

SUPPLEMENTAL_FILENAMES = {
    "asset_allocation": "asset_allocation.csv",
    "geographic_exposure": "geographic_exposure.csv",
    "style": "style.csv",
    "periodic_returns": "periodic_returns.csv",
}


def load_analysis_bundle(folder: str | Path) -> FidelityAnalysisBundle:
    """Load every supported Fidelity analysis CSV from a dated folder."""
    path = Path(folder)
    as_of_date = _parse_folder_date(path)
    return FidelityAnalysisBundle(
        as_of_date=as_of_date,
        source_dir=str(path),
        asset_allocation=load_asset_allocation(path / SUPPLEMENTAL_FILENAMES["asset_allocation"]),
        geographic_exposure=load_geographic_exposure(path / SUPPLEMENTAL_FILENAMES["geographic_exposure"]),
        style_exposure=load_style_exposure(path / SUPPLEMENTAL_FILENAMES["style"]),
        periodic_returns=load_periodic_returns(path / SUPPLEMENTAL_FILENAMES["periodic_returns"]),
    )


def load_analysis_bundles(root: str | Path) -> list[FidelityAnalysisBundle]:
    """Load analysis bundles from every dated child folder under root."""
    root_path = Path(root)
    bundles: list[FidelityAnalysisBundle] = []
    for folder in sorted(root_path.iterdir() if root_path.exists() else []):
        if not folder.is_dir() or not _is_date_folder(folder.name):
            continue
        bundle = load_analysis_bundle(folder)
        if bundle_has_data(bundle):
            bundles.append(bundle)
    return bundles


def bundle_has_data(bundle: FidelityAnalysisBundle) -> bool:
    """Return True when a bundle contains any supplemental Fidelity data."""
    return any(
        (
            bundle.asset_allocation,
            bundle.geographic_exposure,
            bundle.style_exposure,
            bundle.periodic_returns,
        )
    )


def load_asset_allocation(path: str | Path) -> list[AssetAllocationRow]:
    """Load Fidelity asset-allocation rows, ignoring trailing disclosure text."""
    return _load_table_rows(
        path,
        stop_when=lambda row: _first_cell(row) == "Balance and Returns",
        builder=lambda row: AssetAllocationRow(
            symbol=_cell(row, 0).upper(),
            description=_cell(row, 1),
            account=_cell(row, 2),
            asset_class=_cell(row, 3),
            weight_pct=_parse_percent(_cell(row, 4)),
            current_value=_parse_number(_cell(row, 5)),
        ),
        required_cells=6,
    )


def load_geographic_exposure(path: str | Path) -> list[GeographicExposureRow]:
    """Load Fidelity geographic-exposure rows, ignoring trailing disclosure text."""
    return _load_table_rows(
        path,
        stop_when=lambda row: _first_cell(row) == "Balance and Returns",
        builder=lambda row: GeographicExposureRow(
            symbol=_cell(row, 0).upper(),
            description=_cell(row, 1),
            account=_cell(row, 2),
            region=_cell(row, 3),
            country=_cell(row, 4),
            weight_pct=_parse_percent(_cell(row, 5)),
            current_value=_parse_number(_cell(row, 6)),
        ),
        required_cells=7,
    )


def load_style_exposure(path: str | Path) -> list[StyleExposureRow]:
    """Load Fidelity style-exposure rows, ignoring trailing disclosure text."""
    return _load_table_rows(
        path,
        stop_when=lambda row: _first_cell(row) == "Balance and Returns",
        builder=lambda row: StyleExposureRow(
            symbol=_cell(row, 0).upper(),
            description=_cell(row, 1),
            account=_cell(row, 2),
            style=_cell(row, 3),
            weight_pct=_parse_percent(_cell(row, 4)),
            current_value=_parse_number(_cell(row, 5)),
        ),
        required_cells=6,
    )


def load_periodic_returns(path: str | Path) -> list[PeriodicReturnRow]:
    """Load Fidelity periodic return rows from the irregular return export."""
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    rows = _read_csv_rows(csv_path)
    period_end_date = _extract_period_end_date(rows)
    if not period_end_date:
        return []

    parsed: list[PeriodicReturnRow] = []
    current_return_type: Optional[str] = None
    for row in rows:
        first = _first_cell(row)
        if not first:
            continue
        if "Important information about performance returns" in first:
            break
        if first.startswith("Time-weighted rate of return"):
            current_return_type = "time_weighted"
            continue
        if first.startswith("Money-weighted rate of return"):
            current_return_type = "money_weighted"
            continue
        if current_return_type and len(row) >= 10 and not first.startswith("Prior month"):
            parsed.append(
                PeriodicReturnRow(
                    period_end_date=period_end_date,
                    return_type=current_return_type,
                    account=first,
                    one_month_pct=_parse_optional_percent(_cell(row, 1)),
                    three_month_pct=_parse_optional_percent(_cell(row, 2)),
                    ytd_pct=_parse_optional_percent(_cell(row, 3)),
                    one_year_pct=_parse_optional_percent(_cell(row, 4)),
                    three_year_pct=_parse_optional_percent(_cell(row, 5)),
                    five_year_pct=_parse_optional_percent(_cell(row, 6)),
                    ten_year_pct=_parse_optional_percent(_cell(row, 7)),
                    life_pct=_parse_optional_percent(_cell(row, 8)),
                    life_start_date=_parse_month_day_year(_cell(row, 9)),
                )
            )

    return parsed


def summarize_values(rows: Iterable[T], key_getter: Callable[[T], str], value_getter: Callable[[T], float], limit: int = 8) -> list[dict[str, float | str]]:
    """Aggregate value rows into sorted summary records."""
    totals: dict[str, float] = {}
    for row in rows:
        key = key_getter(row) or "Unknown"
        totals[key] = totals.get(key, 0.0) + value_getter(row)

    grand_total = sum(totals.values())
    summary = [
        {
            "name": name,
            "current_value": value,
            "weight_pct": (value / grand_total * 100) if grand_total else 0.0,
        }
        for name, value in totals.items()
    ]
    return sorted(summary, key=lambda item: item["current_value"], reverse=True)[:limit]


def _load_table_rows(
    path: str | Path,
    stop_when: Callable[[list[str]], bool],
    builder: Callable[[list[str]], T],
    required_cells: int,
) -> list[T]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    rows = _read_csv_rows(csv_path)
    parsed: list[T] = []
    for index, row in enumerate(rows):
        if index == 0:
            continue
        if stop_when(row):
            break
        if len(row) < required_cells or not _cell(row, 0):
            continue
        try:
            parsed.append(builder(row))
        except ValueError:
            continue
    return parsed


def _read_csv_rows(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    return list(csv.reader(text.splitlines()))


def _extract_period_end_date(rows: list[list[str]]) -> Optional[date]:
    if not rows:
        return None
    match = re.search(r"as of ([A-Za-z]{3})-(\d{1,2})-(\d{4})", _first_cell(rows[0]))
    if not match:
        return None
    month_text, day_text, year_text = match.groups()
    try:
        month = datetime.strptime(month_text.title(), "%b").month
    except ValueError:
        return None
    return date(int(year_text), month, int(day_text))


def _parse_month_day_year(value: str) -> Optional[date]:
    text = _clean_text(value)
    if not text or text == "--":
        return None
    try:
        return datetime.strptime(text, "%b-%d-%Y").date()
    except ValueError:
        return None


def _parse_folder_date(path: Path) -> date:
    if _is_date_folder(path.name):
        return date.fromisoformat(path.name)
    return date.today()


def _is_date_folder(name: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", name))


def _parse_percent(value: str) -> float:
    return _parse_number(value)


def _parse_optional_percent(value: str) -> Optional[float]:
    text = _clean_text(value)
    if not text or text == "--":
        return None
    return _parse_percent(text)


def _first_cell(row: list[str]) -> str:
    return _cell(row, 0)


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return _clean_text(row[index])