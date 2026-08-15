"""Target schema.

Three tables: the cleaned facts, one row per pipeline run, and the rows that
were rejected during that run. Keeping rejections in the database (rather than
only logging them) is what makes `navigator status` and `navigator rejects`
able to explain a run after the fact.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from navigator.db import Base
from navigator.types import UtcDateTime


class ServiceRequest(Base):
    """A cleaned 311 service request.

    The natural key from the source is the primary key, which is what makes the
    load idempotent: re-loading the same source updates rows in place instead of
    appending duplicates.
    """

    __tablename__ = "service_requests"
    __table_args__ = (
        CheckConstraint(
            "resolution_hours IS NULL OR resolution_hours >= 0",
            name="ck_service_requests_resolution_non_negative",
        ),
        Index("ix_service_requests_created_at", "created_at"),
        Index("ix_service_requests_borough_category", "borough", "complaint_category"),
    )

    unique_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    complaint_type: Mapped[str] = mapped_column(String(128))
    complaint_category: Mapped[str] = mapped_column(String(64), index=True)
    descriptor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    borough: Mapped[str | None] = mapped_column(String(32), nullable=True)
    incident_zip: Mapped[str | None] = mapped_column(String(5), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_closed: Mapped[bool] = mapped_column()
    resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_loaded_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_loaded_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PipelineRun(Base):
    """One execution of the pipeline, with per-stage timings and row counts."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    rows_extracted: Mapped[int] = mapped_column(Integer, default=0)
    rows_valid: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)

    extract_ms: Mapped[int] = mapped_column(Integer, default=0)
    validate_ms: Mapped[int] = mapped_column(Integer, default=0)
    transform_ms: Mapped[int] = mapped_column(Integer, default=0)
    load_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    rejections: Mapped[list["Rejection"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Rejection(Base):
    """A single source row that failed validation, and why.

    One row per (source row, broken rule), so a row that breaks two rules is
    recorded twice and every failure is visible rather than only the first.
    """

    __tablename__ = "rejections"
    __table_args__ = (Index("ix_rejections_run_rule", "run_id", "rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    source_row: Mapped[int] = mapped_column(Integer)
    unique_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)

    run: Mapped["PipelineRun"] = relationship(back_populates="rejections")
