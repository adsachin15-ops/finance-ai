"""
tests/integration/test_auth.py
─────────────────────────────────────────────────────────────
Integration tests for backend/api/routes/auth.py

Tests cover:
  - User registration (success, duplicate, validation)
  - User login (success, wrong PIN, locked account)
  - Guest session creation
  - Logout
  - PIN change
  - /auth/me endpoint
  - Token validation on protected routes
  - Account lockout after 5 failed attempts
"""

from __future__ import annotations

import pytest


# ── Registration Tests ────────────────────────────────────────────

class TestRegister:

    def test_register_success(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "+919876543210",
            "pin": "1234",
            "display_name": "Test User",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["is_guest"] is False
        assert data["user_id"] is not None
        assert data["display_name"] == "Test User"
        assert data["token_type"] == "bearer"

    def test_register_without_display_name(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "+919876543210",
            "pin": "1234",
        })
        assert resp.status_code == 201
        assert resp.json()["display_name"] is None

    def test_register_duplicate_phone(self, client):
        payload = {"phone_number": "+919876543210", "pin": "1234"}
        client.post("/api/v1/auth/register", json=payload)
        resp = client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_register_invalid_phone(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "12345",
            "pin": "1234",
        })
        assert resp.status_code == 422

    def test_register_non_digit_pin(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "+919876543210",
            "pin": "abcd",
        })
        assert resp.status_code == 422

    def test_register_pin_too_short(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "+919876543210",
            "pin": "12",
        })
        assert resp.status_code == 422

    def test_register_pin_too_long(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "+919876543210",
            "pin": "123456789",
        })
        assert resp.status_code == 422

    def test_register_normalizes_phone(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "9876543210",
            "pin": "1234",
        })
        assert resp.status_code == 201

    def test_register_token_is_string(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "phone_number": "+919876543210",
            "pin": "1234",
        })
        assert isinstance(resp.json()["access_token"], str)
        assert len(resp.json()["access_token"]) > 10


# ── Login Tests ───────────────────────────────────────────────────

class TestLogin:

    def test_login_success(self, client, registered_user):
        resp = client.post("/api/v1/auth/login", json={
            "phone_number": "+919876543210",
            "pin": "1234",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["is_guest"] is False

    def test_login_wrong_pin(self, client, registered_user):
        resp = client.post("/api/v1/auth/login", json={
            "phone_number": "+919876543210",
            "pin": "9999",
        })
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "phone_number": "+919999999999",
            "pin": "1234",
        })
        assert resp.status_code == 401

    def test_login_invalid_phone_format(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "phone_number": "invalid",
            "pin": "1234",
        })
        assert resp.status_code == 422

    def test_login_account_lockout_after_5_failures(self, client, registered_user):
        for _ in range(5):
            client.post("/api/v1/auth/login", json={
                "phone_number": "+919876543210",
                "pin": "0000",
            })
        # 6th attempt — should be locked
        resp = client.post("/api/v1/auth/login", json={
            "phone_number": "+919876543210",
            "pin": "1234",
        })
        assert resp.status_code == 423
        assert "locked" in resp.json()["detail"].lower()

    def test_login_returns_new_token_each_time(self, client, registered_user):
        r1 = client.post("/api/v1/auth/login", json={
            "phone_number": "+919876543210", "pin": "1234"
        })
        r2 = client.post("/api/v1/auth/login", json={
            "phone_number": "+919876543210", "pin": "1234"
        })
        assert r1.json()["access_token"] != r2.json()["access_token"]


# ── Guest Session Tests ───────────────────────────────────────────

class TestGuestSession:

    def test_guest_session_created(self, client):
        resp = client.post("/api/v1/auth/guest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_guest"] is True
        assert data["user_id"] is None
        assert data["display_name"] == "Guest"
        assert "access_token" in data

    def test_guest_token_different_each_time(self, client):
        r1 = client.post("/api/v1/auth/guest")
        r2 = client.post("/api/v1/auth/guest")
        assert r1.json()["access_token"] != r2.json()["access_token"]

    def test_guest_token_rejected_on_registered_route(self, client, guest_headers):
        resp = client.get("/api/v1/auth/me", headers=guest_headers)
        assert resp.status_code == 401


# ── Logout Tests ──────────────────────────────────────────────────

class TestLogout:

    def test_logout_success(self, client, auth_headers):
        resp = client.post("/api/v1/auth/logout", headers=auth_headers)
        assert resp.status_code == 204

    def test_logout_without_token(self, client):
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 204

    def test_logout_with_invalid_token(self, client):
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer invalid.token.here.abc"}
        )
        assert resp.status_code == 204


# ── PIN Change Tests ──────────────────────────────────────────────

class TestPinChange:

    def test_change_pin_success(self, client, auth_headers):
        resp = client.post("/api/v1/auth/pin/change", json={
            "current_pin": "1234",
            "new_pin": "5678",
            "confirm_pin": "5678",
        }, headers=auth_headers)
        assert resp.status_code == 204

    def test_change_pin_wrong_current(self, client, auth_headers):
        resp = client.post("/api/v1/auth/pin/change", json={
            "current_pin": "0000",
            "new_pin": "5678",
            "confirm_pin": "5678",
        }, headers=auth_headers)
        assert resp.status_code == 401

    def test_change_pin_mismatch_confirm(self, client, auth_headers):
        resp = client.post("/api/v1/auth/pin/change", json={
            "current_pin": "1234",
            "new_pin": "5678",
            "confirm_pin": "9999",
        }, headers=auth_headers)
        assert resp.status_code == 422

    def test_change_pin_requires_auth(self, client):
        resp = client.post("/api/v1/auth/pin/change", json={
            "current_pin": "1234",
            "new_pin": "5678",
            "confirm_pin": "5678",
        })
        assert resp.status_code == 401

    def test_new_pin_works_after_change(self, client, auth_headers):
        client.post("/api/v1/auth/pin/change", json={
            "current_pin": "1234",
            "new_pin": "5678",
            "confirm_pin": "5678",
        }, headers=auth_headers)

        resp = client.post("/api/v1/auth/login", json={
            "phone_number": "+919876543210",
            "pin": "5678",
        })
        assert resp.status_code == 200


# ── /auth/me Tests ────────────────────────────────────────────────

class TestGetMe:

    def test_get_me_success(self, client, auth_headers):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone_number"] == "+919876543210"
        assert data["display_name"] == "Test User"
        assert "id" in data
        assert "created_at" in data

    def test_get_me_requires_auth(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_get_me_invalid_token(self, client):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer bad.token.value.here"}
        )
        assert resp.status_code == 401

    def test_get_me_no_bearer_prefix(self, client, registered_user):
        token = registered_user["access_token"]
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": token}
        )
        assert resp.status_code == 401


# ── Health Check Tests ────────────────────────────────────────────

class TestHealthCheck:

    def test_health_endpoint_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_structure(self, client):
        data = client.get("/health").json()
        assert "status" in data
        assert "version" in data
        assert "database" in data

    def test_system_info_endpoint(self, client):
        resp = client.get("/api/v1/system/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["app_name"] == "Finance-AI"
        assert "features" in data
