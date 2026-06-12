"""Organize Fidelity export files into dated daily bundles."""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from src.data_ingestion.csv_loader import _extract_fidelity_downloaded_at, _normalize_column_name


CANONICAL_FILENAMES = {
    "positions": "positions.csv",
    "asset_allocation": "asset_allocation.csv",
    "geographic_exposure": "geographic_exposure.csv",
    "style": "style.csv",
    "periodic_returns": "periodic_returns.csv",
}


@dataclass(frozen=True)
class OrganizedFile:
    """A file copied or moved into a dated Fidelity bundle."""

    source_path: Path
    target_path: Path
    file_type: str
    action: str


def organize_fidelity_exports(
    files: Iterable[str | Path],
    snapshots_dir: str | Path,
    bundle_date: date,
    move: bool = False,
) -> list[OrganizedFile]:
    """Copy or move supported Fidelity CSV exports into a dated folder."""
    target_dir = bundle_dir(snapshots_dir, bundle_date)
    target_dir.mkdir(parents=True, exist_ok=True)

    organized: list[OrganizedFile] = []
    for file in files:
        source = Path(file)
        if not source.exists() or not source.is_file():
            continue

        file_type = classify_export(source)
        if not file_type:
            continue

        target = _unique_destination(target_dir / CANONICAL_FILENAMES[file_type])
        if move:
            shutil.move(str(source), str(target))
            action = "moved"
        else:
            shutil.copy2(source, target)
            action = "copied"
        organized.append(
            OrganizedFile(
                source_path=source,
                target_path=target,
                file_type=file_type,
                action=action,
            )
        )

    update_manifest(target_dir, organized, bundle_date)
    return organized


def move_existing_snapshots_for_date(
    snapshots_dir: str | Path,
    bundle_date: date,
) -> list[OrganizedFile]:
    """Move legacy root-level snapshots for a date into that date's bundle folder."""
    root = Path(snapshots_dir)
    target_dir = bundle_dir(root, bundle_date)
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = bundle_date.strftime("snapshot_%Y%m%d_")
    organized: list[OrganizedFile] = []

    for source in sorted(root.glob(f"{prefix}*.json")):
        target = _unique_destination(target_dir / source.name)
        shutil.move(str(source), str(target))
        organized.append(
            OrganizedFile(
                source_path=source,
                target_path=target,
                file_type="snapshot",
                action="moved",
            )
        )

    if organized:
        update_manifest(target_dir, organized, bundle_date)
    return organized


def bundle_dir(snapshots_dir: str | Path, bundle_date: date) -> Path:
    """Return the dated Fidelity bundle directory for a date."""
    return Path(snapshots_dir) / bundle_date.isoformat()


def classify_export(path: str | Path) -> Optional[str]:
    """Classify a Fidelity CSV export by header/content."""
    csv_path = Path(path)
    if csv_path.suffix.lower() != ".csv":
        return None

    rows = _read_preview_rows(csv_path)
    if not rows:
        return None

    first = _first_cell(rows[0])
    if first.startswith("Prior month end performance as of"):
        return "periodic_returns"

    columns = {_normalize_column_name(value) for value in rows[0]}
    if {"accountnumber", "symbol", "quantity"}.issubset(columns):
        return "positions"
    if {"symbol", "assetclass", "weight", "currentvalue"}.issubset(columns):
        return "asset_allocation"
    if {"symbol", "region", "country", "weight", "currentvalue"}.issubset(columns):
        return "geographic_exposure"
    if {"symbol", "style", "weight", "currentvalue"}.issubset(columns):
        return "style"
    return None


def infer_positions_export_datetime(path: str | Path) -> Optional[datetime]:
    """Return the Fidelity positions export timestamp when a footer is present."""
    source_text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    return _extract_fidelity_downloaded_at(source_text)


def update_manifest(
    folder: str | Path,
    organized_files: Iterable[OrganizedFile],
    bundle_date: date,
) -> Path:
    """Append organized file metadata to a bundle manifest."""
    folder_path = Path(folder)
    manifest_path = folder_path / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        data = {
            "bundle_date": bundle_date.isoformat(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": [],
        }

    existing_targets = {item.get("target_path") for item in data.get("files", [])}
    for item in organized_files:
        record = {
            "file_type": item.file_type,
            "action": item.action,
            "source_path": str(item.source_path),
            "target_path": str(item.target_path),
            "target_name": item.target_path.name,
            "organized_at": datetime.now().isoformat(timespec="seconds"),
        }
        if item.file_type == "positions":
            timestamp = infer_positions_export_datetime(item.target_path)
            record["export_datetime"] = timestamp.isoformat(timespec="seconds") if timestamp else None
        if item.file_type == "periodic_returns":
            record["period_end_date"] = _extract_periodic_return_date(item.target_path)
        if record["target_path"] not in existing_targets:
            data.setdefault("files", []).append(record)
            existing_targets.add(record["target_path"])

    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return manifest_path


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _read_preview_rows(path: Path, limit: int = 5) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    rows: list[list[str]] = []
    for row in csv.reader(text.splitlines()):
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _extract_periodic_return_date(path: Path) -> Optional[str]:
    rows = _read_preview_rows(path, limit=1)
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
    return date(int(year_text), month, int(day_text)).isoformat()


def _first_cell(row: list[str]) -> str:
    return row[0].strip() if row else ""