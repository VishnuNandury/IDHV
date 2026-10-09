"""Engine / session plumbing. Portable across SQLite and Postgres (no dialect-specific SQL)."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # Background ingestion threads share the engine; SQLite needs this flag.
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    """Naive UTC timestamp. All DateTime columns store naive UTC (portable SQLite/Postgres)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def isoz(dt: datetime | None) -> str | None:
    """API representation of a timestamp: ISO-8601 UTC with 'Z', second precision (e.g. 2026-09-30T10:00:00Z).
    Naive values are taken as UTC (how they are stored); aware values are converted."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat() + "Z"


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (register tables on Base.metadata)

    Base.metadata.create_all(bind=engine)
