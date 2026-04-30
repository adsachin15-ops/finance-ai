"""
backend/core/database.py
─────────────────────────────────────────────────────────────
Database engine factory.

Registered users  → persistent SQLite (plain, no SQLCipher yet)
Guest sessions    → in-memory SQLite (auto-wiped on session end)
ORM               → SQLAlchemy 2.0 declarative style
Migration         → Alembic

NOTE: SQLCipher requires the sqlcipher3-binary package which needs
system-level libsqlcipher-dev. For Phase 1 we use plain SQLite.
SQLCipher can be dropped in at Phase 2 by replacing the engine
creator. All ORM code stays identical.

Cloud-readiness:
  Replace engine URL with postgresql+asyncpg://... in Phase 4.
  Zero ORM code changes required.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

import sqlalchemy
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.config import get_settings
from backend.core.logger import get_logger

log = get_logger(__name__)
settings = get_settings()


# ── Declarative Base ──────────────────────────────────────────────

class Base(DeclarativeBase):
    """SQLAlchemy declarative base. All models inherit from this."""
    pass


# ── Engine Factory ────────────────────────────────────────────────

def _create_sqlite_engine(db_path: str) -> sqlalchemy.Engine:
    """
    Build a plain SQLite engine for registered users.
    WAL mode + foreign keys enabled on every connection.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=settings.debug,
    )

    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA synchronous = NORMAL")
        dbapi_conn.execute("PRAGMA cache_size = -64000")
        dbapi_conn.execute("PRAGMA temp_store = MEMORY")

    log.info("db.engine.created", path=db_path, encrypted=False)
    return engine


def _create_memory_engine() -> sqlalchemy.Engine:
    """
    In-memory SQLite for guest sessions.
    StaticPool ensures all operations share the same in-memory DB.
    Destroyed automatically when the engine is disposed.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    log.info("db.engine.created", path=":memory:", encrypted=False, mode="guest")
    return engine


# ── Engine Singletons ─────────────────────────────────────────────

_registered_engine: Optional[sqlalchemy.Engine] = None
_guest_engine: Optional[sqlalchemy.Engine] = None
_engine_lock = threading.Lock()


def init_db() -> None:
    """
    Initialize database engines and create all tables.
    Call once from main.py lifespan on application startup.
    Idempotent — safe to call multiple times.
    """
    global _registered_engine, _guest_engine

    with _engine_lock:
        if _registered_engine is None:
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            _registered_engine = _create_sqlite_engine(str(settings.db_path))
            Base.metadata.create_all(bind=_registered_engine)
            log.info("db.init.complete", mode="registered")

        if _guest_engine is None:
            _guest_engine = _create_memory_engine()
            Base.metadata.create_all(bind=_guest_engine)
            log.info("db.init.complete", mode="guest")


def get_registered_engine() -> sqlalchemy.Engine:
    if _registered_engine is None:
        raise RuntimeError("Database not initialized. Call init_db() at startup.")
    return _registered_engine


def get_guest_engine() -> sqlalchemy.Engine:
    if _guest_engine is None:
        raise RuntimeError("Database not initialized. Call init_db() at startup.")
    return _guest_engine


# ── Session Factories ─────────────────────────────────────────────

def _make_session_factory(engine: sqlalchemy.Engine) -> sessionmaker:
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


# ── FastAPI Dependency Injectors ──────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency for registered-user DB sessions.

    Usage:
        @router.get("/transactions")
        def list_transactions(db: Session = Depends(get_db)):
            return db.query(Transaction).all()
    """
    factory = _make_session_factory(get_registered_engine())
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_guest_db() -> Generator[Session, None, None]:
    """FastAPI dependency for guest sessions (in-memory)."""
    factory = _make_session_factory(get_guest_engine())
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def db_session(guest: bool = False) -> Generator[Session, None, None]:
    """
    Context manager for use outside FastAPI (scripts, tests, services).

    Usage:
        with db_session() as db:
            user = db.query(User).first()
    """
    engine = get_guest_engine() if guest else get_registered_engine()
    factory = _make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wipe_guest_db() -> None:
    """
    Destroy all guest session data.
    Called on session logout, app exit, and atexit hook.
    """
    global _guest_engine
    with _engine_lock:
        if _guest_engine:
            Base.metadata.drop_all(bind=_guest_engine)
            Base.metadata.create_all(bind=_guest_engine)
            log.info("db.guest.wiped", reason="session_end")


def check_db_health() -> dict:
    """Health check for /health endpoint."""
    result = {"registered": False, "guest": False}
    try:
        with get_registered_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        result["registered"] = True
    except Exception as e:
        log.error("db.health.failed", engine="registered", error=str(e))

    try:
        with get_guest_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        result["guest"] = True
    except Exception as e:
        log.error("db.health.failed", engine="guest", error=str(e))

    return result
