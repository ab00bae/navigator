"""Stage 4 — the idempotent load."""

from __future__ import annotations

from sqlalchemy import func, select

from navigator.db import SessionLocal, session_scope
from navigator.models import ServiceRequest
from navigator.pipeline.load import CHUNK_SIZE, load
from navigator.pipeline.transform import transform


def count() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(ServiceRequest))


def load_frame(frame) -> tuple[int, int]:
    with session_scope() as session:
        return load(session, frame)


def test_first_load_inserts(db, extracted):
    frame = transform(extracted([{"unique_key": "1"}, {"unique_key": "2"}]))

    assert load_frame(frame) == (2, 0)
    assert count() == 2


def test_reloading_updates_instead_of_duplicating(db, extracted):
    frame = transform(extracted([{"unique_key": "1"}, {"unique_key": "2"}]))
    load_frame(frame)

    inserted, updated = load_frame(frame)

    assert (inserted, updated) == (0, 2)
    assert count() == 2


def test_three_runs_leave_the_same_rows(db, extracted):
    """The idempotency claim, stated as bluntly as it can be."""
    frame = transform(extracted([{"unique_key": str(n)} for n in range(20)]))

    for _ in range(3):
        load_frame(frame)

    assert count() == 20


def test_changed_values_overwrite_the_old_row(db, extracted):
    load_frame(transform(extracted([{"unique_key": "1", "borough": "BROOKLYN"}])))

    load_frame(transform(extracted([{"unique_key": "1", "borough": "QUEENS"}])))

    with SessionLocal() as session:
        assert session.get(ServiceRequest, "1").borough == "Queens"


def test_first_loaded_at_survives_an_update(db, extracted):
    """It records when the row was first seen, so a re-run must not reset it."""
    frame = transform(extracted([{"unique_key": "1"}]))
    load_frame(frame)
    with SessionLocal() as session:
        original = session.get(ServiceRequest, "1").first_loaded_at

    load_frame(frame)

    with SessionLocal() as session:
        row = session.get(ServiceRequest, "1")
        assert row.first_loaded_at == original
        assert row.last_loaded_at >= original


def test_a_partly_new_batch_reports_both_counts(db, extracted):
    load_frame(transform(extracted([{"unique_key": "1"}])))

    inserted, updated = load_frame(
        transform(extracted([{"unique_key": "1"}, {"unique_key": "2"}]))
    )

    assert (inserted, updated) == (1, 1)


def test_batches_larger_than_the_chunk_size_load_completely(db, extracted):
    """Guards the chunking that keeps SQLite under its bound-parameter limit."""
    rows = [{"unique_key": str(n)} for n in range(CHUNK_SIZE * 2 + 25)]
    frame = transform(extracted(rows))

    inserted, _ = load_frame(frame)

    assert inserted == len(rows)
    assert count() == len(rows)


def test_empty_frame_is_a_no_op(db, extracted):
    frame = transform(extracted([{"unique_key": "1"}])).iloc[0:0]

    assert load_frame(frame) == (0, 0)
    assert count() == 0


def test_nulls_are_stored_as_nulls(db, extracted):
    """pandas' NA sentinels must not reach the database as strings."""
    frame = transform(extracted([{"unique_key": "1", "incident_zip": "", "borough": ""}]))

    load_frame(frame)

    with SessionLocal() as session:
        row = session.get(ServiceRequest, "1")
        assert row.incident_zip is None
        assert row.borough is None
