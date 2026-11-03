"""SQLite (WAL) engine and sessions for the API and the in-process job worker.

- The database lives at `DATA_DIR/punchwalk.sqlite3`. Tables are created on first use
  (`SQLModel.metadata.create_all`), so any entry point (API, worker, scripts) can start cold.
- One engine per DATA_DIR path, so tests that point DATA_DIR at a tmp dir get their own database.
- `get_session()` is the FastAPI dependency; `session_factory()` is what the job worker and the
  pipeline runner use (`run_walk(..., session_factory=session_factory)`).
- Sessions use `expire_on_commit=False`, so rows stay readable after a commit.
- SQLite hands datetimes back without a timezone; `as_utc()` puts UTC back on them.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from punchwalk.config import get_settings
from punchwalk.core import models  # noqa: F401  (registers every table on SQLModel.metadata)

DB_FILENAME = "punchwalk.sqlite3"
BUSY_TIMEOUT_S = 30

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def db_path() -> Path:
    return Path(get_settings().DATA_DIR) / DB_FILENAME


def _set_pragmas(dbapi_connection, _record) -> None:
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_S * 1000}")
    finally:
        cur.close()


def get_engine() -> Engine:
    """The engine for the current DATA_DIR (created, with tables, on first call)."""
    path = db_path()
    key = str(path)
    engine = _engines.get(key)
    if engine is not None:
        return engine
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                f"sqlite:///{path}",
                connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_S},
            )
            event.listen(engine, "connect", _set_pragmas)
            SQLModel.metadata.create_all(engine)
            _engines[key] = engine
    return engine


def init_db() -> Engine:
    """Create the database and tables if needed (called at app startup)."""
    return get_engine()


def reset_engines() -> None:
    """Dispose every cached engine (tests switch DATA_DIR between cases)."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def session_factory() -> Session:
    """A new session on the current DATA_DIR's database. The caller closes it (use `with`)."""
    return Session(get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with session_factory() as session:
        yield session


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read; every stored datetime is UTC, so put it back."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
