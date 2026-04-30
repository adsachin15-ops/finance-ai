"""
tests/conftest.py
─────────────────────────────────────────────────────────────
Shared pytest fixtures for all tests.

Fixtures provided:
  settings     → test Settings instance (overrides .env)
  db_engine    → fresh in-memory SQLite engine per test
  db_session   → SQLAlchemy session bound to test engine
  client       → FastAPI TestClient with overridden DB
  auth_headers → Bearer token headers for a registered test user
  guest_headers → Bearer token headers for a guest session

Design:
  - Every test gets a fresh in-memory database.
  - No test ever touches the real database/finance.db file.
  - DB is created and dropped per test function (function scope).
  - TestClient is synchronous — no async test client needed
    because FastAPI TestClient handles async routes internally.
"""

from __future__ import annotations

import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Force test environment before any app imports
os.environ["APP_ENV"] = "testing"
os.environ["DB_ENCRYPTION_KEY"] = "test_key_minimum_32_characters_long!!"
os.environ["SECRET_KEY"] = "test_secret_minimum_32_characters_long_padding!!"
os.environ["LOG_LEVEL"] = "ERROR"
os.environ["LOG_FORMAT"] = "console"
os.environ["LOG_FILE"] = ""

from backend.core.config import get_settings
from backend.core.database import Base, get_db
from backend.main import create_app


# ── Test Engine ───────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db_engine():
    """
    Fresh in-memory SQLite engine per test function.
    All tables created at start, dropped at end.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine) -> Generator[Session, None, None]:
    """
    SQLAlchemy session bound to the test engine.
    Rolls back after each test — no state leaks between tests.
    """
    factory = sessionmaker(
        bind=db_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── Test Client ───────────────────────────────────────────────────

@pytest.fixture(scope="function")
def client(db_engine) -> Generator[TestClient, None, None]:
    """
    FastAPI TestClient with DB dependency overridden to use
    the test in-memory engine. No real DB is touched.
    """
    # Clear settings cache so test env vars are picked up
    get_settings.cache_clear()

    app = create_app()

    # Override get_db to use test engine
    def override_get_db():
        factory = sessionmaker(
            bind=db_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


# ── Auth Helpers ──────────────────────────────────────────────────

@pytest.fixture(scope="function")
def registered_user(client) -> dict:
    """
    Register a test user and return the token response dict.
    """
    resp = client.post("/api/v1/auth/register", json={
        "phone_number": "+919876543210",
        "pin": "1234",
        "display_name": "Test User",
    })
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    return resp.json()


@pytest.fixture(scope="function")
def auth_headers(registered_user) -> dict:
    """
    Authorization headers for a registered test user.
    Usage:
        def test_something(client, auth_headers):
            resp = client.get("/api/v1/accounts/", headers=auth_headers)
    """
    return {"Authorization": f"Bearer {registered_user['access_token']}"}


@pytest.fixture(scope="function")
def guest_headers(client) -> dict:
    """
    Authorization headers for a guest session.
    """
    resp = client.post("/api/v1/auth/guest")
    assert resp.status_code == 200, f"Guest session failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── Sample Data Helpers ───────────────────────────────────────────

@pytest.fixture(scope="function")
def test_account(client, auth_headers) -> dict:
    """
    Create a test savings account. Returns the account dict.
    """
    resp = client.post("/api/v1/accounts/", json={
        "nickname": "Test HDFC",
        "account_type": "savings",
        "bank_name": "HDFC Bank",
        "currency": "INR",
        "current_balance": 10000.0,
    }, headers=auth_headers)
    assert resp.status_code == 201, f"Account creation failed: {resp.text}"
    return resp.json()
