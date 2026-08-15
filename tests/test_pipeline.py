"""End-to-end: the runner, run bookkeeping, and re-run safety."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from navigator.db import SessionLocal
from navigator.models import PipelineRun, Rejection, ServiceRequest
from navigator.pipeline.extract import SchemaError
from navigator.pipeline.runner import run_pipeline


def rows() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(ServiceRequest))


def test_a_clean_run_reports_its_counts(db, make_csv):
    source = make_csv([{"unique_key": "1"}, {"unique_key": "2"}])

    result = run_pipeline(source)

    assert result.status == "success"
    assert result.rows_extracted == 2
    assert result.rows_valid == 2
    assert result.rows_rejected == 0
    assert result.rows_inserted == 2
    assert rows() == 2


def test_bad_rows_are_excluded_but_good_ones_still_load(db, make_csv):
    source = make_csv(
        [{"unique_key": "1"}, {"unique_key": ""}, {"unique_key": "3"}]
    )

    result = run_pipeline(source)

    assert result.rows_extracted == 3
    assert result.rows_rejected == 1
    assert result.rows_inserted == 2
    assert rows() == 2


def test_the_run_is_recorded(db, make_csv):
    source = make_csv([{"unique_key": "1"}])

    result = run_pipeline(source)

    with SessionLocal() as session:
        run = session.get(PipelineRun, result.run_id)
        assert run.status == "success"
        assert run.rows_extracted == 1
        assert run.finished_at is not None
        assert run.total_ms >= 0


def test_rejections_are_persisted_with_their_reason(db, make_csv):
    source = make_csv([{"unique_key": ""}])

    result = run_pipeline(source)

    with SessionLocal() as session:
        stored = list(
            session.scalars(
                select(Rejection).where(Rejection.run_id == result.run_id)
            )
        )
    assert len(stored) == 1
    assert stored[0].rule == "missing_unique_key"
    assert stored[0].source_row == 2


def test_every_stage_is_timed(db, make_csv):
    result = run_pipeline(make_csv([{"unique_key": "1"}]))

    assert set(result.timings) == {"extract", "validate", "transform", "load"}


def test_stage_callback_fires_once_per_stage(db, make_csv):
    seen = []

    run_pipeline(make_csv([{"unique_key": "1"}]),
                 on_stage=lambda stage, detail, ms: seen.append(stage))

    assert seen == ["extract", "validate", "transform", "load"]


class TestReRunning:
    def test_the_same_source_twice_does_not_duplicate(self, db, make_csv):
        source = make_csv([{"unique_key": "1"}, {"unique_key": "2"}])

        run_pipeline(source)
        second = run_pipeline(source)

        assert second.rows_inserted == 0
        assert second.rows_updated == 2
        assert rows() == 2

    def test_each_run_gets_its_own_record(self, db, make_csv):
        source = make_csv([{"unique_key": "1"}])

        first = run_pipeline(source)
        second = run_pipeline(source)

        assert first.run_id != second.run_id
        with SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(PipelineRun)) == 2

    def test_a_later_run_can_add_new_rows(self, db, make_csv):
        run_pipeline(make_csv([{"unique_key": "1"}], name="first.csv"))

        result = run_pipeline(
            make_csv([{"unique_key": "1"}, {"unique_key": "2"}], name="second.csv")
        )

        assert (result.rows_inserted, result.rows_updated) == (1, 1)
        assert rows() == 2


class TestFailures:
    def test_a_broken_source_raises(self, db, make_csv):
        source = make_csv([{"unique_key": "1"}], columns=["unique_key"])

        with pytest.raises(SchemaError):
            run_pipeline(source)

    def test_a_failed_run_is_still_recorded(self, db, make_csv):
        """A crash must leave evidence, or `navigator status` would hide it."""
        source = make_csv([{"unique_key": "1"}], columns=["unique_key"])

        with pytest.raises(SchemaError):
            run_pipeline(source)

        with SessionLocal() as session:
            run = session.scalars(
                select(PipelineRun).order_by(PipelineRun.id.desc())
            ).first()
        assert run.status == "failed"
        assert "SchemaError" in run.error
        assert run.finished_at is not None

    def test_a_failed_run_loads_nothing(self, db, make_csv):
        source = make_csv([{"unique_key": "1"}], columns=["unique_key"])

        with pytest.raises(SchemaError):
            run_pipeline(source)

        assert rows() == 0
