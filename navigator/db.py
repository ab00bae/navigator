"""Engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from navigator.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def configure_engine(url: str) -> None:
    """Point the module at a different database.

    Rebinding the existing sessionmaker rather than replacing it keeps every
    `from navigator.db import SessionLocal` already in place valid. Tests use
    this to give each case its own database.
    """
    global engine
    engine = create_engine(url, future=True)
    SessionLocal.configure(bind=engine)


def create_schema() -> None:
    """Create tables if they do not exist.

    The pipeline owns a small, stable schema, so plain metadata creation is
    enough here; there is no migration history to maintain.
    """
    from navigator import models  # noqa: F401  - registers the tables

    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on failure."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
