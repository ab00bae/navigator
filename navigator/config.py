"""Settings, resolved from the environment with working defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The public Socrata endpoint the sample data came from. Kept here so `navigator
# fetch` and the provenance note in the README cannot drift apart.
SOURCE_API = "https://data.cityofnewyork.us/resource/erm2-nwe9.csv"
SOURCE_COLUMNS = [
    "unique_key",
    "created_date",
    "closed_date",
    "complaint_type",
    "descriptor",
    "borough",
    "incident_zip",
    "status",
    "latitude",
    "longitude",
]


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'navigator.db'}"
    )
    default_source: Path = PROJECT_ROOT / "data" / "raw.csv"
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
    # Rows outside these bounds are not plausible NYC coordinates.
    lat_min: float = 40.4
    lat_max: float = 41.0
    lon_min: float = -74.3
    lon_max: float = -73.6


settings = Settings()
