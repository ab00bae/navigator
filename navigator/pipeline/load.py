"""Stage 4 — load.

Idempotent by construction: the source's natural key is the table's primary key
and rows are upserted, so running the same file twice leaves the same number of
rows behind. The second run reports updates rather than inserts, which is the
signal that re-running was safe.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from navigator.logging_conf import get_logger
from navigator.models import ServiceRequest
from navigator.pipeline.extract import SOURCE_ROW

logger = get_logger("load")

# Every bound parameter counts against SQLite's statement variable limit, so
# rows go in batches rather than as one very wide statement.
CHUNK_SIZE = 400

COLUMNS = [
    "unique_key",
    "created_at",
    "closed_at",
    "complaint_type",
    "complaint_category",
    "descriptor",
    "borough",
    "incident_zip",
    "status",
    "is_closed",
    "resolution_hours",
    "latitude",
    "longitude",
]


def _upsert(dialect: str):
    """The insert construct that supports ON CONFLICT for this backend."""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def _records(frame: pd.DataFrame) -> list[dict]:
    prepared = frame.drop(columns=[SOURCE_ROW], errors="ignore")[COLUMNS]
    # pandas' own NA sentinels are not valid bound parameters; None is.
    return prepared.astype(object).where(pd.notna(prepared), None).to_dict("records")


def load(session: Session, frame: pd.DataFrame) -> tuple[int, int]:
    """Upsert the frame. Returns (inserted, updated)."""
    if frame.empty:
        return 0, 0

    records = _records(frame)
    now = datetime.now(timezone.utc)
    keys = [record["unique_key"] for record in records]

    # Which keys already exist decides insert-vs-update in the report. Chunked
    # for the same reason the writes are.
    existing: set[str] = set()
    for start in range(0, len(keys), CHUNK_SIZE):
        batch = keys[start : start + CHUNK_SIZE]
        existing.update(
            session.scalars(
                select(ServiceRequest.unique_key).where(
                    ServiceRequest.unique_key.in_(batch)
                )
            )
        )

    inserted = sum(1 for key in keys if key not in existing)
    updated = len(keys) - inserted

    insert = _upsert(session.bind.dialect.name)
    for start in range(0, len(records), CHUNK_SIZE):
        batch = records[start : start + CHUNK_SIZE]
        for record in batch:
            record["first_loaded_at"] = now
            record["last_loaded_at"] = now

        statement = insert(ServiceRequest).values(batch)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[ServiceRequest.unique_key],
                # first_loaded_at is deliberately absent: it records when the row
                # was first seen and must survive later runs.
                set_={
                    column: statement.excluded[column]
                    for column in COLUMNS + ["last_loaded_at"]
                    if column != "unique_key"
                },
            )
        )

    logger.info("loaded rows", extra={"inserted": inserted, "updated": updated})
    return inserted, updated
