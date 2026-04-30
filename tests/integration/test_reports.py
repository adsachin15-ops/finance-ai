"""
tests/integration/test_reports.py
─────────────────────────────────────────────────────────────
Integration tests for backend/api/routes/reports.py

Tests cover:
  - CSV export (content, headers, filename, empty state)
  - Summary CSV export (categories, totals row)
  - Excel export (content-type, non-empty, structure)
  - Monthly ZIP export (structure, summary.csv inside)
  - Auth required on all endpoints
  - Period filter presets
  - Date range filters
  - Account filter
  - Multi-user isolation
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import pytest


# ── Helpers ───────────────────────────────────────────────────────

HDFC_HEADERS = [
    "Date", "Narration", "Value Date",
    "Debit Amount", "Credit Amount", "Balance"
]


def _today() -> str:
    return date.today().strftime("%d/%m/%Y")


def _make_csv_bytes(rows, headers=None) -> bytes:
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(headers or HDFC_HEADERS)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


SAMPLE_ROWS = [
    [_today(), "SWIGGY ORDER",   _today(), "450.00",   "",          "9550.00"],
    [_today(), "SALARY CREDIT",  _today(), "",          "50000.00", "59550.00"],
    [_today(), "UBER TRIP",      _today(), "250.00",   "",          "59300.00"],
    [_today(), "AMAZON PURCHASE", _today(), "1299.00", "",          "58251.00"],
    [_today(), "NETFLIX",        _today(), "649.00",   "",          "57602.00"],
    [_today(), "FREELANCE PAY",  _today(), "",          "15000.00", "72602.00"],
]


def _upload(client, auth_headers, account_id, rows=None) -> None:
    content = _make_csv_bytes(rows or SAMPLE_ROWS)
    resp = client.post(
        "/api/v1/upload/file",
        files={"file": ("statement.csv", content, "text/csv")},
        data={"account_id": str(account_id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200


def _register_second_user(client) -> dict:
    resp = client.post("/api/v1/auth/register", json={
        "phone_number": "+919000000097",
        "pin": "4321",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── CSV Export Tests ──────────────────────────────────────────────

class TestCSVExport:

    def test_csv_requires_auth(self, client):
        resp = client.get("/api/v1/reports/csv")
        assert resp.status_code == 401

    def test_csv_empty_state(self, client, auth_headers):
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

    def test_csv_content_type(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        assert "text/csv" in resp.headers["content-type"]

    def test_csv_content_disposition_header(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        assert "attachment" in resp.headers["content-disposition"]
        assert ".csv" in resp.headers["content-disposition"]

    def test_csv_has_correct_columns(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        reader = csv.DictReader(io.StringIO(resp.text))
        assert set(reader.fieldnames) == {
            "id", "date", "amount", "type", "category",
            "subcategory", "merchant", "description", "source",
            "notes", "account_nickname", "account_type", "currency",
        }

    def test_csv_row_count_matches_transactions(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 6

    def test_csv_x_total_records_header(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        assert resp.headers.get("x-total-records") == "6"

    def test_csv_period_monthly(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/csv?period=monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_csv_period_weekly(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/csv?period=weekly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_csv_period_yearly(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/csv?period=yearly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_csv_invalid_period_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/reports/csv?period=quarterly",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_csv_date_range_filter(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        today = date.today().isoformat()
        resp = client.get(
            f"/api/v1/reports/csv?date_from={today}&date_to={today}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        assert len(rows) == 6

    def test_csv_account_filter(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            f"/api/v1/reports/csv?account_id={test_account['id']}",
            headers=auth_headers,
        )
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        for row in rows:
            assert row["account_nickname"] == test_account["nickname"]

    def test_csv_only_own_data(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        headers2 = _register_second_user(client)
        acc2 = client.post("/api/v1/accounts/", json={
            "nickname": "User2 Acc", "account_type": "savings",
        }, headers=headers2).json()
        _upload(client, headers2, acc2["id"])

        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        assert len(rows) == 6  # only own transactions

    def test_csv_debit_amounts_positive(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        for row in rows:
            assert float(row["amount"]) > 0

    def test_csv_types_are_debit_or_credit(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/csv", headers=auth_headers)
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        for row in rows:
            assert row["type"] in ("debit", "credit")


# ── Summary CSV Tests ─────────────────────────────────────────────

class TestSummaryCSV:

    def test_summary_requires_auth(self, client):
        resp = client.get("/api/v1/reports/summary/csv")
        assert resp.status_code == 401

    def test_summary_content_type(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/summary/csv",
            headers=auth_headers,
        )
        assert "text/csv" in resp.headers["content-type"]

    def test_summary_has_correct_columns(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/summary/csv",
            headers=auth_headers,
        )
        reader = csv.DictReader(io.StringIO(resp.text))
        assert set(reader.fieldnames) == {
            "category", "total_amount", "transaction_count",
            "percentage", "avg_transaction",
        }

    def test_summary_has_total_row(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/summary/csv",
            headers=auth_headers,
        )
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        categories = [r["category"] for r in rows]
        assert "TOTAL" in categories

    def test_summary_percentages_sum_to_100(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/summary/csv",
            headers=auth_headers,
        )
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        # Exclude TOTAL row
        data_rows = [r for r in rows if r["category"] != "TOTAL"]
        total_pct = sum(float(r["percentage"]) for r in data_rows)
        assert abs(total_pct - 100.0) < 1.0

    def test_summary_only_debits(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/summary/csv",
            headers=auth_headers,
        )
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        total_row = next(r for r in rows if r["category"] == "TOTAL")
        # Total should match sum of debits only
        # Swiggy(450) + Uber(250) + Amazon(1299) + Netflix(649) = 2648
        assert float(total_row["total_amount"]) == 2648.0

    def test_summary_period_filter(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/summary/csv?period=monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ── Excel Export Tests ────────────────────────────────────────────

class TestExcelExport:

    def test_excel_requires_auth(self, client):
        resp = client.get("/api/v1/reports/excel")
        assert resp.status_code == 401

    def test_excel_content_type(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["content-type"]

    def test_excel_content_disposition(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        assert "attachment" in resp.headers["content-disposition"]
        assert ".xlsx" in resp.headers["content-disposition"]

    def test_excel_non_empty_response(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        assert len(resp.content) > 1000  # xlsx has minimum size

    def test_excel_is_valid_xlsx(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        import pandas as pd
        workbook = pd.read_excel(io.BytesIO(resp.content), sheet_name=None)
        assert "Transactions" in workbook
        assert "Category Summary" in workbook
        assert "Monthly Trend" in workbook

    def test_excel_transactions_sheet_row_count(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        import pandas as pd
        df = pd.read_excel(io.BytesIO(resp.content), sheet_name="Transactions")
        assert len(df) == 6

    def test_excel_empty_state_returns_200(self, client, auth_headers):
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        assert resp.status_code == 200

    def test_excel_period_filter(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/excel?period=monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_excel_x_total_records_header(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get("/api/v1/reports/excel", headers=auth_headers)
        assert resp.headers.get("x-total-records") == "6"


# ── Monthly ZIP Tests ─────────────────────────────────────────────

class TestMonthlyZIP:

    def test_zip_requires_auth(self, client):
        resp = client.get("/api/v1/reports/monthly")
        assert resp.status_code == 401

    def test_zip_content_type(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "zip" in resp.headers["content-type"]

    def test_zip_content_disposition(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        assert "attachment" in resp.headers["content-disposition"]
        assert ".zip" in resp.headers["content-disposition"]

    def test_zip_is_valid_archive(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert len(names) >= 1

    def test_zip_contains_summary_csv(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "summary.csv" in zf.namelist()

    def test_zip_contains_monthly_csv(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        monthly_files = [n for n in names if n.startswith("transactions_")]
        assert len(monthly_files) >= 1

    def test_zip_monthly_csv_has_correct_columns(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        monthly_file = next(n for n in names if n.startswith("transactions_"))
        content = zf.read(monthly_file).decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        assert "date" in reader.fieldnames
        assert "amount" in reader.fieldnames
        assert "type" in reader.fieldnames

    def test_zip_year_filter(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        year = date.today().year
        resp = client.get(
            f"/api/v1/reports/monthly?year={year}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_zip_invalid_year_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/reports/monthly?year=1999",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_zip_x_total_months_header(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        assert "x-total-months" in resp.headers

    def test_zip_x_total_records_header(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/reports/monthly",
            headers=auth_headers,
        )
        assert resp.headers.get("x-total-records") == "6"
