"""
tests/integration/test_accounts.py
─────────────────────────────────────────────────────────────
Integration tests for backend/api/routes/accounts.py

Tests cover:
  - List accounts (empty, with data, inactive filter)
  - Create account (all types, validation, duplicate nickname)
  - Get single account (found, not found, wrong user)
  - Update account (fields, partial update)
  - Delete account (soft delete, preserves transactions)
  - Account summary (totals, empty)
  - Multi-user isolation (user A cannot see user B accounts)
  - Credit utilization calculation
"""

from __future__ import annotations

import pytest


# ── Helpers ───────────────────────────────────────────────────────

def _create_account(client, auth_headers, **kwargs) -> dict:
    """Create an account with defaults overridable by kwargs."""
    payload = {
        "nickname": "Test Account",
        "account_type": "savings",
        "bank_name": "HDFC Bank",
        "currency": "INR",
        "current_balance": 10000.0,
    }
    payload.update(kwargs)
    resp = client.post(
        "/api/v1/accounts/",
        json=payload,
        headers=auth_headers,
    )
    return resp


def _register_second_user(client) -> dict:
    """Register a second user and return auth headers."""
    resp = client.post("/api/v1/auth/register", json={
        "phone_number": "+919000000099",
        "pin": "4321",
        "display_name": "User Two",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── List Accounts Tests ───────────────────────────────────────────

class TestListAccounts:

    def test_list_empty_initially(self, client, auth_headers):
        resp = client.get("/api/v1/accounts/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_accounts(self, client, auth_headers):
        _create_account(client, auth_headers, nickname="Account 1")
        _create_account(client, auth_headers, nickname="Account 2")
        resp = client.get("/api/v1/accounts/", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_excludes_inactive_by_default(self, client, auth_headers):
        r = _create_account(client, auth_headers, nickname="Active")
        _create_account(client, auth_headers, nickname="ToDelete")
        account_id = client.get(
            "/api/v1/accounts/", headers=auth_headers
        ).json()[1]["id"]
        client.delete(f"/api/v1/accounts/{account_id}", headers=auth_headers)

        resp = client.get("/api/v1/accounts/", headers=auth_headers)
        assert len(resp.json()) == 1

    def test_list_includes_inactive_when_requested(self, client, auth_headers):
        _create_account(client, auth_headers, nickname="Active")
        acc_id = _create_account(
            client, auth_headers, nickname="ToDelete"
        ).json()["id"]
        client.delete(f"/api/v1/accounts/{acc_id}", headers=auth_headers)

        resp = client.get(
            "/api/v1/accounts/?include_inactive=true",
            headers=auth_headers,
        )
        assert len(resp.json()) == 2

    def test_list_requires_auth(self, client):
        resp = client.get("/api/v1/accounts/")
        assert resp.status_code == 401

    def test_list_only_own_accounts(self, client, auth_headers):
        _create_account(client, auth_headers, nickname="My Account")
        headers2 = _register_second_user(client)
        _create_account(client, headers2, nickname="Their Account")

        resp = client.get("/api/v1/accounts/", headers=auth_headers)
        assert len(resp.json()) == 1
        assert resp.json()[0]["nickname"] == "My Account"


# ── Create Account Tests ──────────────────────────────────────────

class TestCreateAccount:

    def test_create_savings_account(self, client, auth_headers):
        resp = _create_account(client, auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["nickname"] == "Test Account"
        assert data["account_type"] == "savings"
        assert data["current_balance"] == 10000.0

    def test_create_credit_card_account(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            nickname="HDFC Credit",
            account_type="credit_card",
            credit_limit=100000.0,
            current_balance=85000.0,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["account_type"] == "credit_card"
        assert data["credit_limit"] == 100000.0
        assert data["credit_utilization"] is not None

    def test_create_wallet_account(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            nickname="Paytm Wallet",
            account_type="wallet",
            bank_name=None,
        )
        assert resp.status_code == 201

    def test_create_upi_account(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            nickname="GPay UPI",
            account_type="upi",
        )
        assert resp.status_code == 201

    def test_create_cash_account(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            nickname="Cash",
            account_type="cash",
            bank_name=None,
        )
        assert resp.status_code == 201

    def test_create_with_last_four_digits(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            last_four_digits="1234",
        )
        assert resp.status_code == 201
        assert resp.json()["last_four_digits"] == "1234"

    def test_create_invalid_account_type(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            account_type="invalid_type",
        )
        assert resp.status_code == 422

    def test_create_duplicate_nickname_rejected(self, client, auth_headers):
        _create_account(client, auth_headers, nickname="My Bank")
        resp = _create_account(client, auth_headers, nickname="My Bank")
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_create_requires_auth(self, client):
        resp = client.post("/api/v1/accounts/", json={
            "nickname": "Test",
            "account_type": "savings",
        })
        assert resp.status_code == 401

    def test_create_response_has_id(self, client, auth_headers):
        resp = _create_account(client, auth_headers)
        assert "id" in resp.json()
        assert isinstance(resp.json()["id"], int)

    def test_create_response_has_user_id(self, client, auth_headers):
        resp = _create_account(client, auth_headers)
        assert "user_id" in resp.json()

    def test_create_currency_uppercased(self, client, auth_headers):
        resp = _create_account(client, auth_headers, currency="inr")
        assert resp.json()["currency"] == "INR"

    def test_create_multiple_accounts_allowed(self, client, auth_headers):
        for i in range(5):
            resp = _create_account(
                client, auth_headers, nickname=f"Account {i}"
            )
            assert resp.status_code == 201


# ── Get Single Account Tests ──────────────────────────────────────

class TestGetAccount:

    def test_get_existing_account(self, client, auth_headers, test_account):
        resp = client.get(
            f"/api/v1/accounts/{test_account['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == test_account["id"]

    def test_get_nonexistent_account(self, client, auth_headers):
        resp = client.get("/api/v1/accounts/99999", headers=auth_headers)
        assert resp.status_code == 404

    def test_get_other_user_account_returns_404(self, client, auth_headers):
        headers2 = _register_second_user(client)
        acc = _create_account(client, headers2, nickname="Their Account").json()

        resp = client.get(
            f"/api/v1/accounts/{acc['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_get_requires_auth(self, client, test_account):
        resp = client.get(f"/api/v1/accounts/{test_account['id']}")
        assert resp.status_code == 401


# ── Update Account Tests ──────────────────────────────────────────

class TestUpdateAccount:

    def test_update_nickname(self, client, auth_headers, test_account):
        resp = client.put(
            f"/api/v1/accounts/{test_account['id']}",
            json={"nickname": "Updated Name"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["nickname"] == "Updated Name"

    def test_update_balance(self, client, auth_headers, test_account):
        resp = client.put(
            f"/api/v1/accounts/{test_account['id']}",
            json={"current_balance": 25000.0},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["current_balance"] == 25000.0

    def test_update_partial_fields_only(self, client, auth_headers, test_account):
        original_type = test_account["account_type"]
        resp = client.put(
            f"/api/v1/accounts/{test_account['id']}",
            json={"nickname": "New Name"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # account_type should not change
        assert resp.json()["account_type"] == original_type

    def test_update_nonexistent_account(self, client, auth_headers):
        resp = client.put(
            "/api/v1/accounts/99999",
            json={"nickname": "New"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_update_requires_auth(self, client, test_account):
        resp = client.put(
            f"/api/v1/accounts/{test_account['id']}",
            json={"nickname": "New"},
        )
        assert resp.status_code == 401

    def test_update_other_user_account_rejected(
        self, client, auth_headers
    ):
        headers2 = _register_second_user(client)
        acc = _create_account(client, headers2, nickname="Theirs").json()
        resp = client.put(
            f"/api/v1/accounts/{acc['id']}",
            json={"nickname": "Hijacked"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── Delete Account Tests ──────────────────────────────────────────

class TestDeleteAccount:

    def test_delete_account_returns_204(self, client, auth_headers, test_account):
        resp = client.delete(
            f"/api/v1/accounts/{test_account['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    def test_deleted_account_not_in_list(self, client, auth_headers, test_account):
        client.delete(
            f"/api/v1/accounts/{test_account['id']}",
            headers=auth_headers,
        )
        resp = client.get("/api/v1/accounts/", headers=auth_headers)
        ids = [a["id"] for a in resp.json()]
        assert test_account["id"] not in ids

    def test_delete_is_soft_not_hard(self, client, auth_headers, test_account):
        client.delete(
            f"/api/v1/accounts/{test_account['id']}",
            headers=auth_headers,
        )
        resp = client.get(
            f"/api/v1/accounts/?include_inactive=true",
            headers=auth_headers,
        )
        ids = [a["id"] for a in resp.json()]
        assert test_account["id"] in ids

    def test_delete_nonexistent_returns_404(self, client, auth_headers):
        resp = client.delete("/api/v1/accounts/99999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client, test_account):
        resp = client.delete(f"/api/v1/accounts/{test_account['id']}")
        assert resp.status_code == 401


# ── Account Summary Tests ─────────────────────────────────────────

class TestAccountSummary:

    def test_summary_empty_account(self, client, auth_headers, test_account):
        resp = client.get(
            f"/api/v1/accounts/{test_account['id']}/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_transactions"] == 0
        assert data["total_debits"] == 0.0
        assert data["total_credits"] == 0.0

    def test_summary_structure(self, client, auth_headers, test_account):
        resp = client.get(
            f"/api/v1/accounts/{test_account['id']}/summary",
            headers=auth_headers,
        )
        data = resp.json()
        assert "account_id" in data
        assert "nickname" in data
        assert "account_type" in data
        assert "current_balance" in data
        assert "total_transactions" in data
        assert "total_debits" in data
        assert "total_credits" in data

    def test_summary_requires_auth(self, client, test_account):
        resp = client.get(
            f"/api/v1/accounts/{test_account['id']}/summary"
        )
        assert resp.status_code == 401

    def test_summary_nonexistent_account(self, client, auth_headers):
        resp = client.get(
            "/api/v1/accounts/99999/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── Credit Utilization Tests ──────────────────────────────────────

class TestCreditUtilization:

    def test_credit_card_has_utilization(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            nickname="Credit Card",
            account_type="credit_card",
            credit_limit=100000.0,
            current_balance=75000.0,
        )
        data = resp.json()
        assert data["credit_utilization"] is not None
        assert data["credit_utilization"] == 25.0

    def test_savings_account_no_utilization(self, client, auth_headers):
        resp = _create_account(client, auth_headers)
        assert resp.json()["credit_utilization"] is None

    def test_full_credit_utilization(self, client, auth_headers):
        resp = _create_account(
            client, auth_headers,
            nickname="Maxed Card",
            account_type="credit_card",
            credit_limit=50000.0,
            current_balance=0.0,
        )
        assert resp.json()["credit_utilization"] == 100.0
