"""
tests/integration/test_transactions.py
─────────────────────────────────────────────────────────────
Integration tests for backend/api/routes/transactions.py

Tests cover:
  - Paginated list with filters (account, category, type, date)
  - Full-text search
  - Get single transaction
  - Update transaction (category correction, notes)
  - Delete transaction
  - Pagination correctness
  - Multi-user isolation
  - Empty result handling
"""

from __future__ import annotations

import csv
import io

import pytest


# ── Helpers ───────────────────────────────────────────────────────

HDFC_HEADERS = [
    "Date", "Narration", "Value Date",
    "Debit Amount", "Credit Amount", "Balance"
]

SAMPLE_ROWS = [
    ["15/01/2024", "SWIGGY ORDER 123",   "15/01/2024", "450.00",   "",          "9550.00"],
    ["16/01/2024", "SALARY CREDIT",      "16/01/2024", "",          "50000.00", "59550.00"],
    ["17/01/2024", "UBER TRIP MUMBAI",   "17/01/2024", "250.00",   "",          "59300.00"],
    ["18/01/2024", "AMAZON PURCHASE",    "18/01/2024", "1299.00",  "",          "58001.00"],
    ["19/01/2024", "NETFLIX MONTHLY",    "19/01/2024", "649.00",   "",          "57352.00"],
    ["20/01/2024", "HDFC BANK CHARGES",  "20/01/2024", "118.00",   "",          "57234.00"],
    ["21/01/2024", "BIGBASKET ORDER",    "21/01/2024", "2340.00",  "",          "54894.00"],
    ["22/01/2024", "IRCTC TICKET",       "22/01/2024", "1450.00",  "",          "53444.00"],
    ["23/01/2024", "FREELANCE PAYMENT",  "23/01/2024", "",          "15000.00", "68444.00"],
    ["24/01/2024", "APOLLO PHARMACY",    "24/01/2024", "850.00",   "",          "67594.00"],
]


def _make_csv_bytes(rows=None, headers=None) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers or HDFC_HEADERS)
    writer.writerows(rows or SAMPLE_ROWS)
    return buf.getvalue().encode("utf-8")


def _upload_transactions(client, auth_headers, account_id, rows=None) -> dict:
    content = _make_csv_bytes(rows=rows)
    resp = client.post(
        "/api/v1/upload/file",
        files={"file": ("statement.csv", content, "text/csv")},
        data={"account_id": str(account_id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    return resp.json()


def _register_second_user(client) -> dict:
    resp = client.post("/api/v1/auth/register", json={
        "phone_number": "+919000000098",
        "pin": "4321",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── List Transactions Tests ───────────────────────────────────────

class TestListTransactions:

    def test_list_empty_initially(self, client, auth_headers, test_account):
        resp = client.get("/api/v1/transactions/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert resp.json()["items"] == []

    def test_list_after_upload(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/transactions/", headers=auth_headers)
        assert resp.json()["total"] == 10

    def test_list_response_structure(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data

    def test_list_requires_auth(self, client):
        resp = client.get("/api/v1/transactions/")
        assert resp.status_code == 401

    def test_filter_by_account_id(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            f"/api/v1/transactions/?account_id={test_account['id']}",
            headers=auth_headers,
        )
        assert resp.json()["total"] == 10
        for item in resp.json()["items"]:
            assert item["account_id"] == test_account["id"]

    def test_filter_by_type_debit(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/?type=debit",
            headers=auth_headers,
        )
        for item in resp.json()["items"]:
            assert item["type"] == "debit"

    def test_filter_by_type_credit(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/?type=credit",
            headers=auth_headers,
        )
        for item in resp.json()["items"]:
            assert item["type"] == "credit"

    def test_filter_by_date_range(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/?date_from=2024-01-15&date_to=2024-01-17",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 3
        for item in data["items"]:
            assert "2024-01-1" in item["date"]

    def test_filter_by_category(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/?category=Food",
            headers=auth_headers,
        )
        for item in resp.json()["items"]:
            assert item["category"] == "Food"

    def test_list_only_own_transactions(
        self, client, auth_headers, test_account
    ):
        _upload_transactions(client, auth_headers, test_account["id"])

        headers2 = _register_second_user(client)
        acc2 = client.post("/api/v1/accounts/", json={
            "nickname": "User2 Account",
            "account_type": "savings",
        }, headers=headers2).json()
        _upload_transactions(client, headers2, acc2["id"])

        resp = client.get("/api/v1/transactions/", headers=auth_headers)
        assert resp.json()["total"] == 10

        resp2 = client.get("/api/v1/transactions/", headers=headers2)
        assert resp2.json()["total"] == 10


# ── Pagination Tests ──────────────────────────────────────────────

class TestPagination:

    def test_default_page_size_50(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()
        assert data["page_size"] == 50

    def test_custom_page_size(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/transactions/?page_size=3",
            headers=auth_headers,
        ).json()
        assert len(data["items"]) == 3
        assert data["page_size"] == 3

    def test_page_2_different_from_page_1(
        self, client, auth_headers, test_account
    ):
        _upload_transactions(client, auth_headers, test_account["id"])
        p1 = client.get(
            "/api/v1/transactions/?page=1&page_size=5",
            headers=auth_headers,
        ).json()["items"]
        p2 = client.get(
            "/api/v1/transactions/?page=2&page_size=5",
            headers=auth_headers,
        ).json()["items"]
        ids_p1 = {t["id"] for t in p1}
        ids_p2 = {t["id"] for t in p2}
        assert ids_p1.isdisjoint(ids_p2)

    def test_total_pages_calculated(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/transactions/?page_size=3",
            headers=auth_headers,
        ).json()
        assert data["total_pages"] >= 3

    def test_total_count_correct(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/transactions/?page_size=2",
            headers=auth_headers,
        ).json()
        assert data["total"] == 10


# ── Search Tests ──────────────────────────────────────────────────

class TestSearchTransactions:

    def test_search_by_description(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/search?q=swiggy",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1

    def test_search_case_insensitive(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/search?q=SWIGGY",
            headers=auth_headers,
        )
        assert len(resp.json()) >= 1

    def test_search_no_results(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/search?q=XYZNOTFOUND999",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_too_short_query(self, client, auth_headers):
        resp = client.get(
            "/api/v1/transactions/search?q=X",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_search_requires_auth(self, client):
        resp = client.get("/api/v1/transactions/search?q=swiggy")
        assert resp.status_code == 401

    def test_search_respects_limit(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/transactions/search?q=order&limit=1",
            headers=auth_headers,
        )
        assert len(resp.json()) <= 1


# ── Get Single Transaction Tests ──────────────────────────────────

class TestGetTransaction:

    def test_get_existing_transaction(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        txns = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"]
        tx_id = txns[0]["id"]

        resp = client.get(
            f"/api/v1/transactions/{tx_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == tx_id

    def test_get_nonexistent_transaction(self, client, auth_headers):
        resp = client.get(
            "/api/v1/transactions/99999",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_get_other_user_transaction_returns_404(
        self, client, auth_headers, test_account
    ):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        headers2 = _register_second_user(client)
        resp = client.get(
            f"/api/v1/transactions/{tx_id}",
            headers=headers2,
        )
        assert resp.status_code == 404

    def test_get_transaction_structure(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        data = client.get(
            f"/api/v1/transactions/{tx_id}", headers=auth_headers
        ).json()
        assert "id" in data
        assert "date" in data
        assert "amount" in data
        assert "type" in data
        assert "category" in data
        assert "source" in data


# ── Update Transaction Tests ──────────────────────────────────────

class TestUpdateTransaction:

    def test_update_category(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        resp = client.put(
            f"/api/v1/transactions/{tx_id}",
            json={"category": "Shopping"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["category"] == "Shopping"

    def test_update_notes(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        resp = client.put(
            f"/api/v1/transactions/{tx_id}",
            json={"notes": "Team lunch expense"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Team lunch expense"

    def test_update_multiple_fields(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        resp = client.put(
            f"/api/v1/transactions/{tx_id}",
            json={"category": "Food", "notes": "Work lunch"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "Food"
        assert data["notes"] == "Work lunch"

    def test_update_nonexistent_returns_404(self, client, auth_headers):
        resp = client.put(
            "/api/v1/transactions/99999",
            json={"notes": "test"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_update_requires_auth(self, client, test_account):
        resp = client.put(
            "/api/v1/transactions/1",
            json={"notes": "test"},
        )
        assert resp.status_code == 401


# ── Delete Transaction Tests ──────────────────────────────────────

class TestDeleteTransaction:

    def test_delete_transaction(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        resp = client.delete(
            f"/api/v1/transactions/{tx_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    def test_deleted_transaction_not_found(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        client.delete(
            f"/api/v1/transactions/{tx_id}", headers=auth_headers
        )
        resp = client.get(
            f"/api/v1/transactions/{tx_id}", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_delete_reduces_total(self, client, auth_headers, test_account):
        _upload_transactions(client, auth_headers, test_account["id"])
        tx_id = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["items"][0]["id"]

        client.delete(
            f"/api/v1/transactions/{tx_id}", headers=auth_headers
        )
        total = client.get(
            "/api/v1/transactions/", headers=auth_headers
        ).json()["total"]
        assert total == 9

    def test_delete_nonexistent_returns_404(self, client, auth_headers):
        resp = client.delete(
            "/api/v1/transactions/99999",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/v1/transactions/1")
        assert resp.status_code == 401
