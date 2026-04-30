"""
tests/integration/test_upload.py
─────────────────────────────────────────────────────────────
Integration tests for backend/api/routes/upload.py

Tests cover:
  - CSV file upload success
  - Transaction insertion after upload
  - Duplicate file detection
  - Duplicate transaction deduplication
  - Invalid file type rejection
  - File too large rejection
  - Upload without account
  - Upload to wrong user account
  - Upload log creation and retrieval
  - Processing stats correctness
"""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

import pytest


# ── CSV Helpers ───────────────────────────────────────────────────

def _make_csv_bytes(rows: list[list], headers: list[str]) -> bytes:
    """Build CSV file content as bytes."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


HDFC_HEADERS = [
    "Date", "Narration", "Value Date",
    "Debit Amount", "Credit Amount", "Balance"
]

SAMPLE_ROWS = [
    ["15/01/2024", "SWIGGY ORDER 123",  "15/01/2024", "450.00",   "",          "9550.00"],
    ["16/01/2024", "SALARY CREDIT",     "16/01/2024", "",          "50000.00", "59550.00"],
    ["17/01/2024", "UBER TRIP MUMBAI",  "17/01/2024", "250.00",   "",          "59300.00"],
    ["18/01/2024", "AMAZON PURCHASE",   "18/01/2024", "1299.00",  "",          "58001.00"],
    ["19/01/2024", "NETFLIX MONTHLY",   "19/01/2024", "649.00",   "",          "57352.00"],
]


def _upload_csv(client, auth_headers, account_id, rows=None, headers=None):
    """Helper to upload a CSV file and return the response."""
    content = _make_csv_bytes(
        rows=rows or SAMPLE_ROWS,
        headers=headers or HDFC_HEADERS,
    )
    return client.post(
        "/api/v1/upload/file",
        files={"file": ("statement.csv", content, "text/csv")},
        data={"account_id": str(account_id)},
        headers=auth_headers,
    )


# ── Upload Success Tests ──────────────────────────────────────────

class TestUploadSuccess:

    def test_upload_csv_returns_200(self, client, auth_headers, test_account):
        resp = _upload_csv(client, auth_headers, test_account["id"])
        assert resp.status_code == 200

    def test_upload_result_structure(self, client, auth_headers, test_account):
        resp = _upload_csv(client, auth_headers, test_account["id"])
        data = resp.json()
        assert "upload_log_id" in data
        assert "records_parsed" in data
        assert "records_inserted" in data
        assert "records_duplicate" in data
        assert "records_failed" in data
        assert "processing_time_ms" in data
        assert "status" in data

    def test_upload_inserts_correct_count(self, client, auth_headers, test_account):
        resp = _upload_csv(client, auth_headers, test_account["id"])
        data = resp.json()
        assert data["records_parsed"] == 5
        assert data["records_inserted"] == 5
        assert data["records_duplicate"] == 0
        assert data["records_failed"] == 0

    def test_upload_status_completed(self, client, auth_headers, test_account):
        resp = _upload_csv(client, auth_headers, test_account["id"])
        assert resp.json()["status"] == "completed"

    def test_transactions_exist_after_upload(self, client, auth_headers, test_account):
        _upload_csv(client, auth_headers, test_account["id"])
        resp = client.get(
            f"/api/v1/transactions/?account_id={test_account['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    def test_transactions_have_categories(self, client, auth_headers, test_account):
        _upload_csv(client, auth_headers, test_account["id"])
        resp = client.get(
            f"/api/v1/transactions/?account_id={test_account['id']}",
            headers=auth_headers,
        )
        items = resp.json()["items"]
        for item in items:
            assert item["category"] is not None
            assert item["category"] != ""

    def test_debit_credit_types_correct(self, client, auth_headers, test_account):
        _upload_csv(client, auth_headers, test_account["id"])
        resp = client.get(
            f"/api/v1/transactions/?account_id={test_account['id']}",
            headers=auth_headers,
        )
        items = resp.json()["items"]
        types = {item["type"] for item in items}
        assert "debit" in types
        assert "credit" in types

    def test_processing_time_is_positive(self, client, auth_headers, test_account):
        resp = _upload_csv(client, auth_headers, test_account["id"])
        assert resp.json()["processing_time_ms"] > 0


# ── Duplicate Detection Tests ─────────────────────────────────────

class TestDuplicateDetection:

    def test_same_file_upload_twice_rejected(self, client, auth_headers, test_account):
        content = _make_csv_bytes(SAMPLE_ROWS, HDFC_HEADERS)
        # First upload
        client.post(
            "/api/v1/upload/file",
            files={"file": ("statement.csv", content, "text/csv")},
            data={"account_id": str(test_account["id"])},
            headers=auth_headers,
        )
        # Second upload — same file
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("statement.csv", content, "text/csv")},
            data={"account_id": str(test_account["id"])},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "already uploaded" in resp.json()["detail"].lower()

    def test_same_transactions_different_file_deduplicated(
        self, client, auth_headers, test_account
    ):
        # Upload same transactions in two different files
        _upload_csv(client, auth_headers, test_account["id"], rows=SAMPLE_ROWS)

        # Different file content (add whitespace) but same transactions
        rows_copy = [r[:] for r in SAMPLE_ROWS]
        rows_copy[0][1] = "SWIGGY ORDER 123 "  # trailing space — different file hash
        resp = _upload_csv(
            client, auth_headers, test_account["id"], rows=rows_copy
        )
        data = resp.json()
        # Most transactions should be detected as duplicates
        assert data["records_duplicate"] > 0


# ── Validation Tests ──────────────────────────────────────────────

class TestUploadValidation:

    def test_invalid_file_type_rejected(self, client, auth_headers, test_account):
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("script.exe", b"MZ\x90\x00", "application/octet-stream")},
            data={"account_id": str(test_account["id"])},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert "not allowed" in resp.json()["detail"]

    def test_file_too_large_rejected(self, client, auth_headers, test_account):
        large_content = b"a" * (11 * 1024 * 1024)  # 11MB
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("big.csv", large_content, "text/csv")},
            data={"account_id": str(test_account["id"])},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"]

    def test_upload_requires_auth(self, client, test_account):
        content = _make_csv_bytes(SAMPLE_ROWS, HDFC_HEADERS)
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("statement.csv", content, "text/csv")},
            data={"account_id": str(test_account["id"])},
        )
        assert resp.status_code == 401

    def test_upload_to_nonexistent_account(self, client, auth_headers):
        content = _make_csv_bytes(SAMPLE_ROWS, HDFC_HEADERS)
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("statement.csv", content, "text/csv")},
            data={"account_id": "99999"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_upload_to_other_user_account_rejected(
        self, client, auth_headers, test_account
    ):
        # Register second user
        r2 = client.post("/api/v1/auth/register", json={
            "phone_number": "+919000000001",
            "pin": "4321",
        })
        headers2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        # Try to upload to first user's account using second user's token
        content = _make_csv_bytes(SAMPLE_ROWS, HDFC_HEADERS)
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("statement.csv", content, "text/csv")},
            data={"account_id": str(test_account["id"])},
            headers=headers2,
        )
        assert resp.status_code == 404

    def test_empty_csv_returns_completed_with_zero(
        self, client, auth_headers, test_account
    ):
        content = _make_csv_bytes([], HDFC_HEADERS)
        resp = client.post(
            "/api/v1/upload/file",
            files={"file": ("empty.csv", content, "text/csv")},
            data={"account_id": str(test_account["id"])},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["records_inserted"] == 0
        assert data["status"] == "completed"


# ── Upload Log Tests ──────────────────────────────────────────────

class TestUploadLogs:

    def test_upload_log_created(self, client, auth_headers, test_account):
        _upload_csv(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/upload/logs", headers=auth_headers)
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) >= 1

    def test_upload_log_structure(self, client, auth_headers, test_account):
        _upload_csv(client, auth_headers, test_account["id"])
        log = client.get("/api/v1/upload/logs", headers=auth_headers).json()[0]
        assert "id" in log
        assert "file_name" in log
        assert "file_type" in log
        assert "status" in log
        assert "records_inserted" in log
        assert "upload_date" in log

    def test_upload_log_file_name_correct(self, client, auth_headers, test_account):
        _upload_csv(client, auth_headers, test_account["id"])
        log = client.get("/api/v1/upload/logs", headers=auth_headers).json()[0]
        assert log["file_name"] == "statement.csv"

    def test_get_single_upload_log(self, client, auth_headers, test_account):
        upload_resp = _upload_csv(client, auth_headers, test_account["id"])
        log_id = upload_resp.json()["upload_log_id"]
        resp = client.get(f"/api/v1/upload/logs/{log_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == log_id

    def test_upload_log_not_visible_to_other_user(
        self, client, auth_headers, test_account
    ):
        upload_resp = _upload_csv(client, auth_headers, test_account["id"])
        log_id = upload_resp.json()["upload_log_id"]

        r2 = client.post("/api/v1/auth/register", json={
            "phone_number": "+919000000002",
            "pin": "4321",
        })
        headers2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        resp = client.get(f"/api/v1/upload/logs/{log_id}", headers=headers2)
        assert resp.status_code == 404

    def test_upload_logs_requires_auth(self, client):
        resp = client.get("/api/v1/upload/logs")
        assert resp.status_code == 401

    def test_multiple_uploads_all_logged(self, client, auth_headers, test_account):
        # Upload 3 different files
        for i in range(3):
            rows = [[f"1{i}/01/2024", f"TXN {i}", f"1{i}/01/2024", f"{(i+1)*100}.00", "", "9000.00"]]
            _upload_csv(client, auth_headers, test_account["id"], rows=rows)

        logs = client.get("/api/v1/upload/logs", headers=auth_headers).json()
        assert len(logs) == 3
