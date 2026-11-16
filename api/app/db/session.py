"""Engine / session wiring.

★P1-1: SQLite ignores foreign keys unless they are switched on **per
connection**, so the PRAGMAs live in a ``connect`` event hook rather than in a
one-shot startup statement. ★P1-2: schema is owned by Alembic — there is
deliberately no ORM `create-all` helper here (R-14: that helper is allowed only in
``tests/conftest.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings

_settings = get_settings()


def _ensure_sqlite_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        return
    raw = database_url[len("sqlite:///") :]
    if raw.startswith("file:"):
        return
    Path(raw).parent.mkdir(parents=True, exist_ok=True)


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or _settings.database_url
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        _ensure_sqlite_dir(url)
    engine = create_engine(
        url,
        future=True,
        echo=False,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {},
    )
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


engine: Engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def fts_available() -> bool:
    """`PRAGMA compile_options` probe, cached per process (★V4-P1-8).

    Kept here (not in `search_index`) so a caller that only has a database URL can
    answer "can this build do full-text search at all?" without importing the whole
    indexing stack.
    """
    from ..services.search_index import detect_fts

    return detect_fts()
