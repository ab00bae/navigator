"""Sequences the four stages, times each one, and records the run.

The run row is written *before* any work starts and updated as the pipeline
progresses, so a crash still leaves a `failed` row with the error attached
rather than no trace at all. `navigator status` can therefore report failures,
which is the whole point of tracking runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from navigator.db import session_scope
from navigator.logging_conf import get_logger
from navigator.models import PipelineRun, Rejection
from navigator.pipeline.extract import extract
from navigator.pipeline.load import load
from navigator.pipeline.transform import transform
from navigator.pipeline.validate import Rejected, validate

logger = get_logger("runner")

StageCallback = Callable[[str, str, int], None]

STAGES = ("extract", "validate", "transform", "load")


@dataclass
class RunResult:
    run_id: int
    source: str
    status: str
    rows_extracted: int = 0
    rows_valid: int = 0
    rows_rejected: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    timings: dict[str, int] = field(default_factory=dict)
    rejections: list[Rejected] = field(default_factory=list)
    error: str | None = None

    @property
    def total_ms(self) -> int:
        return sum(self.timings.get(stage, 0) for stage in STAGES)


class _Timer:
    """Records elapsed milliseconds per stage and reports each as it finishes."""

    def __init__(self, timings: dict[str, int], on_stage: StageCallback | None) -> None:
        self.timings = timings
        self.on_stage = on_stage

    def run(self, stage: str, work: Callable[[], object], describe) -> object:
        start = time.perf_counter()
        result = work()
        elapsed = int((time.perf_counter() - start) * 1000)
        self.timings[stage] = elapsed
        if self.on_stage:
            self.on_stage(stage, describe(result), elapsed)
        return result


def run_pipeline(source: str | Path, on_stage: StageCallback | None = None) -> RunResult:
    started = datetime.now(timezone.utc)

    with session_scope() as session:
        run = PipelineRun(source=str(source), status="running", started_at=started)
        session.add(run)
        session.flush()
        run_id = run.id

    logger.info("run started", extra={"run_id": run_id, "source": str(source)})
    result = RunResult(run_id=run_id, source=str(source), status="running")
    timer = _Timer(result.timings, on_stage)

    try:
        raw = timer.run("extract", lambda: extract(source), lambda f: f"{len(f):,} rows")
        result.rows_extracted = len(raw)

        validated = timer.run(
            "validate",
            lambda: validate(raw),
            lambda v: f"{len(v.valid):,} valid / {v.rejected_row_count:,} rejected",
        )
        result.rows_valid = len(validated.valid)
        result.rows_rejected = validated.rejected_row_count
        result.rejections = validated.rejections

        cleaned = timer.run(
            "transform", lambda: transform(validated.valid), lambda f: f"{len(f):,} rows"
        )

        def _load() -> tuple[int, int]:
            with session_scope() as session:
                return load(session, cleaned)

        inserted, updated = timer.run(
            "load", _load, lambda r: f"{r[0]:,} inserted / {r[1]:,} updated"
        )
        result.rows_inserted, result.rows_updated = inserted, updated
        result.status = "success"

    except Exception as exc:
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        logger.error("run failed", extra={"run_id": run_id, "error": result.error})
        _finalise(result, started)
        raise

    _finalise(result, started)
    logger.info(
        "run finished",
        extra={
            "run_id": run_id,
            "status": result.status,
            "rows_loaded": result.rows_inserted + result.rows_updated,
            "total_ms": result.total_ms,
        },
    )
    return result


def _finalise(result: RunResult, started: datetime) -> None:
    """Persist the outcome, including rejections, in one transaction."""
    with session_scope() as session:
        run = session.get(PipelineRun, result.run_id)
        run.status = result.status
        run.finished_at = datetime.now(timezone.utc)
        run.rows_extracted = result.rows_extracted
        run.rows_valid = result.rows_valid
        run.rows_rejected = result.rows_rejected
        run.rows_inserted = result.rows_inserted
        run.rows_updated = result.rows_updated
        run.extract_ms = result.timings.get("extract", 0)
        run.validate_ms = result.timings.get("validate", 0)
        run.transform_ms = result.timings.get("transform", 0)
        run.load_ms = result.timings.get("load", 0)
        run.total_ms = result.total_ms
        run.error = result.error

        session.add_all(
            Rejection(
                run_id=result.run_id,
                source_row=rejection.source_row,
                unique_key=rejection.unique_key,
                rule=rejection.rule,
                message=rejection.message,
            )
            for rejection in result.rejections
        )
