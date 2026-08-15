"""Stage 3 — transform.

Pure: the same input frame always yields the same output frame, with no clock,
no database and no randomness involved. That is what makes the pipeline safe to
re-run, and it means the transform tests need no fixtures beyond a DataFrame.

Timestamps are the interesting part. The source publishes naive local New York
wall-clock times, so they are localised to America/New_York and converted to
UTC. Storing them as-is would put a summer record four hours out from a winter
one.
"""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo

import pandas as pd

from navigator.logging_conf import get_logger
from navigator.pipeline.extract import SOURCE_ROW

logger = get_logger("transform")

NYC = ZoneInfo("America/New_York")

# Values the city uses to mean "we don't know", which are more honestly NULL.
PLACEHOLDERS = {"unspecified", "n/a", "na", "none", "null", "", "-"}

# 119 distinct complaint types is too granular to group by, so they roll up into
# a small set of categories. First match wins, so order matters.
CATEGORY_RULES: list[tuple[str, str]] = [
    (r"^noise", "Noise"),
    (r"heat|hot water", "Heat & Hot Water"),
    # Must precede the parks rule: "Illegal Parking" contains "park".
    (r"illegal parking|blocked driveway|vehicle|derelict|driveway", "Parking & Vehicles"),
    # Word boundaries matter here: an unanchored "tree" also matches "street",
    # and an unanchored "park" also matches "parking".
    (r"\btree|\bbranch|\bpark\b|playground", "Trees & Parks"),
    (r"street|sidewalk|pothole|traffic|highway|curb", "Streets & Sidewalks"),
    (r"water|sewer|leak|hydrant", "Water & Sewer"),
    (
        r"rodent|sanitation|unsanitary|dumping|dirty|garbage|litter|graffiti|"
        r"recycling|sweeping|disposal",
        "Sanitation",
    ),
    (r"homeless|encampment", "Homeless Services"),
    (
        r"electric|elevator|plumbing|door|window|paint|appliance|construction|"
        r"flooring|stairs|safety|mold|asbestos",
        "Building Conditions",
    ),
    (r"animal|dog|bird", "Animals"),
]


def _clean_text(series: pd.Series) -> pd.Series:
    """Trim, and turn the city's placeholder values into real nulls."""
    trimmed = series.astype("string").str.strip()
    is_placeholder = trimmed.str.lower().isin(PLACEHOLDERS)
    return trimmed.mask(is_placeholder | trimmed.isna(), other=pd.NA)


def categorise(complaint_type: pd.Series) -> pd.Series:
    lowered = complaint_type.astype("string").str.lower().fillna("")
    category = pd.Series("Other", index=complaint_type.index, dtype="object")
    assigned = pd.Series(False, index=complaint_type.index)

    for pattern, label in CATEGORY_RULES:
        matches = lowered.str.contains(pattern, regex=True, na=False) & ~assigned
        category = category.mask(matches, label)
        assigned = assigned | matches

    return category


def _to_utc(series: pd.Series) -> pd.Series:
    """Read naive New York wall-clock time and return UTC."""
    naive = pd.to_datetime(series, errors="coerce", format="ISO8601")
    localised = naive.dt.tz_localize(
        NYC,
        # The hour repeated by the autumn clock change is read as daylight time,
        # and the hour skipped in spring is nudged forward, so a DST boundary
        # cannot abort a whole run.
        ambiguous=True,
        nonexistent="shift_forward",
    )
    return localised.dt.tz_convert("UTC")


def transform(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)

    out["unique_key"] = frame["unique_key"].str.strip()
    out["created_at"] = _to_utc(frame["created_date"])
    out["closed_at"] = _to_utc(frame["closed_date"])

    out["complaint_type"] = frame["complaint_type"].str.strip()
    out["complaint_category"] = categorise(out["complaint_type"])
    out["descriptor"] = _clean_text(frame["descriptor"])

    borough = _clean_text(frame["borough"])
    out["borough"] = borough.str.title()
    out["status"] = _clean_text(frame["status"])

    # A ZIP that is not five digits carries no information; keep the row, drop
    # the field.
    zip_code = _clean_text(frame["incident_zip"])
    out["incident_zip"] = zip_code.where(
        zip_code.str.fullmatch(r"\d{5}").fillna(False), other=pd.NA
    )

    out["is_closed"] = out["closed_at"].notna()
    elapsed = (out["closed_at"] - out["created_at"]).dt.total_seconds() / 3600
    out["resolution_hours"] = elapsed.round(2)

    out["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    out["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")

    out[SOURCE_ROW] = frame[SOURCE_ROW]

    logger.info(
        "transformed rows",
        extra={
            "rows": len(out),
            "closed": int(out["is_closed"].sum()),
            "categories": int(out["complaint_category"].nunique()),
        },
    )
    return out
