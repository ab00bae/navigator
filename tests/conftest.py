"""Shared fixtures.

Each test gets its own SQLite file, so nothing leaks between cases and the
suite never touches the developer's real navigator.db.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pytest

from navigator.db import configure_engine, create_schema
from navigator.pipeline.extract import REQUIRED_COLUMNS

# A row that passes every validation rule. Tests override only the field under
# test, so a new rule cannot silently invalidate every existing fixture.
VALID_ROW: dict[str, str] = {
    "unique_key": "1000",
    "created_date": "2026-08-12T01:00:00.000",
    "closed_date": "",
    "complaint_type": "Noise - Residential",
    "descriptor": "Loud Music/Party",
    "borough": "BROOKLYN",
    "incident_zip": "11203",
    "status": "Open",
    "latitude": "40.6465",
    "longitude": "-73.9452",
}


@pytest.fixture
def db(tmp_path: Path) -> None:
    configure_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema()


@pytest.fixture
def make_csv(tmp_path: Path) -> Callable[..., Path]:
    """Write a CSV from partial rows, filling the rest with valid defaults."""

    def _make(
        rows: list[dict[str, str]],
        *,
        columns: list[str] | None = None,
        name: str = "raw.csv",
    ) -> Path:
        fieldnames = REQUIRED_COLUMNS if columns is None else columns
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                merged = {**VALID_ROW, **row}
                writer.writerow({name_: merged.get(name_, "") for name_ in fieldnames})
        return path

    return _make


@pytest.fixture
def extracted(make_csv):
    """Build a CSV and return it already through the extract stage."""
    from navigator.pipeline.extract import extract

    def _extracted(rows: list[dict[str, str]]):
        return extract(make_csv(rows))

    return _extracted
