"""Stage 1 — extract.

Reads the source verbatim and checks only that the expected columns are present.
Nothing is parsed or cleaned here: a value that cannot be interpreted is the
validate stage's problem, and keeping this stage dumb means a malformed source
produces a precise schema error instead of a pandas traceback.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from navigator.logging_conf import get_logger

logger = get_logger("extract")

# The contract the pipeline expects from any source it is pointed at.
REQUIRED_COLUMNS = [
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

# Carries the 1-based line number of each row in the source file, so a rejection
# can point the reader at the exact line to go and look at.
SOURCE_ROW = "__source_row"


class SchemaError(Exception):
    """The source does not match the expected column contract."""


def extract(source: str | Path) -> pd.DataFrame:
    path = Path(source)
    if not path.exists():
        raise SchemaError(
            f"Source file not found: {path}. "
            f"Pass --source, or run 'navigator fetch' to download a fresh extract."
        )

    # Everything is read as text; type interpretation belongs to later stages.
    frame = pd.read_csv(path, dtype=str)

    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise SchemaError(
            f"Source is missing required column(s): {', '.join(missing)}. "
            f"Columns present: {', '.join(frame.columns) or '(none)'}"
        )

    # Take the contract columns in a fixed order and ignore any extras, so an
    # upstream addition cannot change the shape of what flows downstream.
    frame = frame[REQUIRED_COLUMNS].copy()

    # +2: one for the header line, one to make it 1-based.
    frame[SOURCE_ROW] = range(2, len(frame) + 2)

    logger.info("extracted rows", extra={"rows": len(frame), "source": path.name})
    return frame
