"""
database/migrations/env.py
─────────────────────────────────────────────────────────────
Alembic migration environment.

Responsibilities:
  1. Connect to the database using app settings
  2. Import all models so metadata is populated
  3. Support both offline (SQL generation) and online (live DB) modes

Offline mode:
  Generates raw SQL statements without connecting to DB.
  Use: alembic upgrade head --sql > migration.sql

Online mode:
  Connects to the real DB and runs migrations directly.
  Use: alembic upgrade head

Cloud-readiness:
  In Phase 4, change DB_URL in .env to PostgreSQL.
  This env.py reads from settings — zero changes needed here.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Path Setup ────────────────────────────────────────────────────
# Add project root to sys.path so backend imports work
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── App Imports ───────────────────────────────────────────────────
# Must import settings before models
os.environ.setdefault("APP_ENV", "development")

from backend.core.config import get_settings
from backend.core.database import Base

# Import ALL models so Base.metadata knows about every table.
# If you add a new model, import it here.
import backend.models.user          # noqa: F401
import backend.models.account       # noqa: F401
import backend.models.transaction   # noqa: F401
import backend.models.upload_log    # noqa: F401
import backend.models.session       # noqa: F401
import backend.models.reminder      # noqa: F401
import backend.models.insight       # noqa: F401
import backend.models.category      # noqa: F401

# ── Alembic Config ────────────────────────────────────────────────
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate support
target_metadata = Base.metadata

# ── DB URL Resolution ─────────────────────────────────────────────

def get_db_url() -> str:
    """
    Build the database URL from app settings.

    Phase 1: SQLite (local file)
    Phase 4: PostgreSQL (cloud) — just change DB_URL in .env

    Returns:
        SQLAlchemy-compatible database URL string.
    """
    settings = get_settings()
    db_path = Path(settings.db_path)

    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Use absolute path for SQLite to avoid CWD issues
    return f"sqlite:///{db_path.resolve()}"


# ── Offline Mode ──────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """
    Run migrations in offline mode.

    Generates SQL without a live DB connection.
    Useful for reviewing changes before applying them.

    Usage:
        alembic upgrade head --sql
    """
    url = get_db_url()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite-specific: render ALTER TABLE as DROP+CREATE
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Online Mode ───────────────────────────────────────────────────

def run_migrations_online() -> None:
    """
    Run migrations in online mode (live DB connection).

    Usage:
        alembic upgrade head
        alembic downgrade -1
    """
    # Override the sqlalchemy.url from alembic.ini with our settings
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_db_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # new connection per migration run
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # render_as_batch=True is required for SQLite.
            # SQLite does not support ALTER COLUMN or DROP COLUMN.
            # Alembic uses batch mode to work around this:
            # it creates a new table, copies data, drops old table,
            # and renames the new one.
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# ── Entry Point ───────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
