"""Stage 1 — the source contract."""

from __future__ import annotations

import pytest

from navigator.pipeline.extract import (
    REQUIRED_COLUMNS,
    SOURCE_ROW,
    SchemaError,
    extract,
)


def test_reads_every_row(make_csv):
    path = make_csv([{"unique_key": "1"}, {"unique_key": "2"}])

    assert len(extract(path)) == 2


def test_missing_file_names_the_path_and_the_way_out(tmp_path):
    missing = tmp_path / "nope.csv"

    with pytest.raises(SchemaError) as exc_info:
        extract(missing)

    message = str(exc_info.value)
    assert "nope.csv" in message
    assert "navigator fetch" in message


def test_missing_column_is_named_in_the_error(make_csv):
    columns = [c for c in REQUIRED_COLUMNS if c != "borough"]
    path = make_csv([{"unique_key": "1"}], columns=columns)

    with pytest.raises(SchemaError) as exc_info:
        extract(path)

    assert "borough" in str(exc_info.value)


def test_every_missing_column_is_reported_at_once(make_csv):
    """One run should tell you everything that is wrong, not just the first thing."""
    columns = [c for c in REQUIRED_COLUMNS if c not in {"borough", "status"}]
    path = make_csv([{"unique_key": "1"}], columns=columns)

    with pytest.raises(SchemaError) as exc_info:
        extract(path)

    message = str(exc_info.value)
    assert "borough" in message and "status" in message


def test_unexpected_columns_are_dropped(make_csv):
    path = make_csv([{"unique_key": "1"}], columns=[*REQUIRED_COLUMNS, "agency"])

    frame = extract(path)

    assert "agency" not in frame.columns
    assert list(frame.columns) == [*REQUIRED_COLUMNS, SOURCE_ROW]


def test_source_row_points_at_the_line_in_the_file(make_csv):
    """Row numbers must account for the header so they match what an editor shows."""
    path = make_csv([{"unique_key": "a"}, {"unique_key": "b"}, {"unique_key": "c"}])

    frame = extract(path)

    assert list(frame[SOURCE_ROW]) == [2, 3, 4]


def test_values_are_left_as_text(make_csv):
    """Interpreting types is a later stage's job; extract must not guess."""
    path = make_csv([{"unique_key": "0071", "latitude": "40.6"}])

    frame = extract(path)

    assert frame["unique_key"].iloc[0] == "0071"
    assert isinstance(frame["latitude"].iloc[0], str)
