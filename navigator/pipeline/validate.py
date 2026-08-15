"""Stage 2 — validate.

Splits extracted rows into those safe to transform and those that are not, with
a named rule and a specific message for every rejection. A row is rejected only
when it is genuinely unusable: a broken identity, or a self-contradictory
timeline. Merely missing detail (no ZIP, no coordinates, an "Unspecified"
borough) is normalised later rather than thrown away.

A row that breaks two rules is reported twice, so the run report shows every
reason the row failed instead of only the first one found.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from navigator.config import settings
from navigator.logging_conf import get_logger
from navigator.pipeline.extract import SOURCE_ROW

logger = get_logger("validate")


@dataclass(frozen=True)
class Rejected:
    source_row: int
    unique_key: str | None
    rule: str
    message: str


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    rejections: list[Rejected]

    @property
    def rejected_row_count(self) -> int:
        """Distinct rows rejected — not the number of broken rules."""
        return len({rejection.source_row for rejection in self.rejections})


def _blank(series: pd.Series) -> pd.Series:
    """True where a value is missing or whitespace-only."""
    return series.isna() | (series.fillna("").str.strip() == "")


def _parse(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", format="ISO8601")


def validate(frame: pd.DataFrame) -> ValidationResult:
    rejections: list[Rejected] = []
    failed = pd.Series(False, index=frame.index)

    keys = frame["unique_key"]
    created_raw, closed_raw = frame["created_date"], frame["closed_date"]
    created_ts, closed_ts = _parse(created_raw), _parse(closed_raw)

    def reject(mask: pd.Series, rule: str, message: callable) -> None:
        nonlocal failed
        if not mask.any():
            return
        for index in frame.index[mask]:
            key = keys.get(index)
            rejections.append(
                Rejected(
                    source_row=int(frame.at[index, SOURCE_ROW]),
                    unique_key=None if pd.isna(key) else str(key).strip() or None,
                    rule=rule,
                    message=message(index),
                )
            )
        failed = failed | mask

    missing_key = _blank(keys)
    reject(missing_key, "missing_unique_key", lambda _: "unique_key is empty")

    # Only meaningful for rows that actually have a key.
    duplicate = ~missing_key & keys.str.strip().duplicated(keep="first")
    reject(
        duplicate,
        "duplicate_unique_key",
        lambda i: f"unique_key {keys[i].strip()} already appeared earlier in the source",
    )

    missing_created = _blank(created_raw)
    reject(missing_created, "missing_created_date", lambda _: "created_date is empty")

    bad_created = ~missing_created & created_ts.isna()
    reject(
        bad_created,
        "unparseable_created_date",
        lambda i: f"created_date {created_raw[i]!r} is not an ISO-8601 timestamp",
    )

    bad_closed = ~_blank(closed_raw) & closed_ts.isna()
    reject(
        bad_closed,
        "unparseable_closed_date",
        lambda i: f"closed_date {closed_raw[i]!r} is not an ISO-8601 timestamp",
    )

    # A ticket cannot be closed before it was opened; such a row would produce a
    # negative resolution time and quietly corrupt any duration analysis.
    backwards = created_ts.notna() & closed_ts.notna() & (closed_ts < created_ts)
    reject(
        backwards,
        "closed_before_created",
        lambda i: (
            f"closed_date {closed_ts[i]:%Y-%m-%d %H:%M:%S} precedes "
            f"created_date {created_ts[i]:%Y-%m-%d %H:%M:%S}"
        ),
    )

    reject(
        _blank(frame["complaint_type"]),
        "missing_complaint_type",
        lambda _: "complaint_type is empty",
    )

    latitude = pd.to_numeric(frame["latitude"], errors="coerce")
    longitude = pd.to_numeric(frame["longitude"], errors="coerce")
    out_of_range = (
        latitude.notna()
        & longitude.notna()
        & (
            (latitude < settings.lat_min)
            | (latitude > settings.lat_max)
            | (longitude < settings.lon_min)
            | (longitude > settings.lon_max)
        )
    )
    reject(
        out_of_range,
        "coordinates_out_of_range",
        lambda i: (
            f"({latitude[i]:.4f}, {longitude[i]:.4f}) is outside New York City "
            f"[{settings.lat_min}..{settings.lat_max}, "
            f"{settings.lon_min}..{settings.lon_max}]"
        ),
    )

    result = ValidationResult(valid=frame[~failed].copy(), rejections=rejections)
    logger.info(
        "validated rows",
        extra={
            "valid": len(result.valid),
            "rejected": result.rejected_row_count,
            "violations": len(rejections),
        },
    )
    return result
