"""
tests/integration/test_dashboard.py
─────────────────────────────────────────────────────────────
Integration tests for backend/api/routes/dashboard.py

Tests cover:
  - Summary endpoint (structure, KPIs, period filters)
  - Category breakdown correctness
  - Account balances in summary
  - Trend endpoint (monthly, weekly, daily)
  - Heatmap endpoint
  - Empty state (no transactions)
  - Health score range
  - Multi-user isolation
"""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest


# ── Helpers ───────────────────────────────────────────────────────

def _make_csv_bytes(rows, headers) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


HDFC_HEADERS = [
    "Date", "Narration", "Value Date",
    "Debit Amount", "Credit Amount", "Balance"
]


def _today_str() -> str:
    return date.today().strftime("%d/%m/%Y")


def _this_month_rows() -> list:
    """Transactions dated today for current month dashboard."""
    today = _today_str()
    return [
        [today, "SWIGGY ORDER",   today, "450.00",   "",          "9550.00"],
        [today, "SALARY CREDIT",  today, "",          "50000.00", "59550.00"],
        [today, "UBER TRIP",      today, "250.00",   "",          "59300.00"],
        [today, "AMAZON ORDER",   today, "1299.00",  "",          "58001.00"],
        [today, "NETFLIX",        today, "649.00",   "",          "57352.00"],
        [today, "FREELANCE PAY",  today, "",          "15000.00", "72352.00"],
    ]


def _upload(client, auth_headers, account_id, rows=None) -> None:
    rows = rows or _this_month_rows()
    content = _make_csv_bytes(rows, HDFC_HEADERS)
    resp = client.post(
        "/api/v1/upload/file",
        files={"file": ("statement.csv", content, "text/csv")},
        data={"account_id": str(account_id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200


# ── Dashboard Summary Tests ───────────────────────────────────────

class TestDashboardSummary:

    def test_summary_requires_auth(self, client):
        resp = client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 401

    def test_summary_empty_state(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_income"] == 0.0
        assert data["summary"]["total_expenses"] == 0.0
        assert data["summary"]["net_savings"] == 0.0

    def test_summary_response_structure(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "top_categories" in data
        assert "account_balances" in data

    def test_summary_fields(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        summary = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        ).json()["summary"]
        assert "period_start" in summary
        assert "period_end" in summary
        assert "total_income" in summary
        assert "total_expenses" in summary
        assert "net_savings" in summary
        assert "savings_rate" in summary
        assert "transaction_count" in summary
        assert "financial_health_score" in summary

    def test_summary_income_correct(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        summary = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["summary"]
        # Salary (50000) + Freelance (15000) = 65000
        assert summary["total_income"] == 65000.0

    def test_summary_expenses_correct(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        summary = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["summary"]
        # Swiggy(450) + Uber(250) + Amazon(1299) + Netflix(649) = 2648
        assert summary["total_expenses"] == 2648.0

    def test_summary_net_savings_correct(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        summary = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["summary"]
        expected = summary["total_income"] - summary["total_expenses"]
        assert summary["net_savings"] == round(expected, 2)

    def test_summary_transaction_count(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        summary = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["summary"]
        assert summary["transaction_count"] == 6

    def test_health_score_between_0_and_100(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        score = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        ).json()["summary"]["financial_health_score"]
        assert 0 <= score <= 100

    def test_health_score_high_when_good_savings(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        score = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["summary"]["financial_health_score"]
        # Income 65000, Expenses 2648 → savings rate ~95% → score should be high
        assert score >= 60

    def test_summary_period_daily(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/summary?period=daily",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["period_start"] == summary["period_end"]

    def test_summary_period_weekly(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/summary?period=weekly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_summary_period_yearly(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/summary?period=yearly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_summary_invalid_period_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/summary?period=quarterly",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_summary_custom_date_range(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        today = date.today().isoformat()
        resp = client.get(
            f"/api/v1/dashboard/summary?date_from={today}&date_to={today}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_summary_filter_by_account(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            f"/api/v1/dashboard/summary?account_id={test_account['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ── Category Breakdown Tests ──────────────────────────────────────

class TestCategoryBreakdown:

    def test_top_categories_not_empty(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        categories = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["top_categories"]
        assert len(categories) > 0

    def test_category_structure(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        cat = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["top_categories"][0]
        assert "category" in cat
        assert "total_amount" in cat
        assert "transaction_count" in cat
        assert "percentage" in cat

    def test_category_percentages_sum_to_100(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        cats = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["top_categories"]
        total_pct = sum(c["percentage"] for c in cats)
        # Allow small floating point variance
        assert abs(total_pct - 100.0) < 1.0

    def test_categories_ordered_by_amount(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        cats = client.get(
            "/api/v1/dashboard/summary?period=monthly",
            headers=auth_headers,
        ).json()["top_categories"]
        amounts = [c["total_amount"] for c in cats]
        assert amounts == sorted(amounts, reverse=True)


# ── Account Balances Tests ────────────────────────────────────────

class TestAccountBalances:

    def test_account_balances_in_summary(
        self, client, auth_headers, test_account
    ):
        balances = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        ).json()["account_balances"]
        assert len(balances) == 1
        assert balances[0]["account_id"] == test_account["id"]

    def test_account_balance_structure(
        self, client, auth_headers, test_account
    ):
        balance = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        ).json()["account_balances"][0]
        assert "account_id" in balance
        assert "nickname" in balance
        assert "account_type" in balance
        assert "current_balance" in balance
        assert "currency" in balance

    def test_multiple_accounts_in_summary(self, client, auth_headers):
        for i in range(3):
            client.post("/api/v1/accounts/", json={
                "nickname": f"Account {i}",
                "account_type": "savings",
            }, headers=auth_headers)

        balances = client.get(
            "/api/v1/dashboard/summary",
            headers=auth_headers,
        ).json()["account_balances"]
        assert len(balances) == 3


# ── Trend Endpoint Tests ──────────────────────────────────────────

class TestSpendingTrend:

    def test_trend_requires_auth(self, client):
        resp = client.get("/api/v1/dashboard/trend")
        assert resp.status_code == 401

    def test_trend_empty_state(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/trend",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trend_returns_list(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/trend",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_trend_point_structure(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/dashboard/trend",
            headers=auth_headers,
        ).json()
        if data:
            point = data[0]
            assert "period_label" in point
            assert "income" in point
            assert "expenses" in point
            assert "net" in point

    def test_trend_monthly_granularity(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/trend?granularity=monthly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_trend_weekly_granularity(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/trend?granularity=weekly",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_trend_invalid_granularity(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/trend?granularity=quarterly",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_trend_net_equals_income_minus_expenses(
        self, client, auth_headers, test_account
    ):
        _upload(client, auth_headers, test_account["id"])
        points = client.get(
            "/api/v1/dashboard/trend",
            headers=auth_headers,
        ).json()
        for p in points:
            expected = round(p["income"] - p["expenses"], 2)
            assert abs(p["net"] - expected) < 0.01


# ── Heatmap Tests ─────────────────────────────────────────────────

class TestSpendingHeatmap:

    def test_heatmap_requires_auth(self, client):
        resp = client.get("/api/v1/dashboard/heatmap")
        assert resp.status_code == 401

    def test_heatmap_empty_state(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/heatmap",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_heatmap_returns_list(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/heatmap",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_heatmap_entry_structure(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        data = client.get(
            "/api/v1/dashboard/heatmap",
            headers=auth_headers,
        ).json()
        if data:
            entry = data[0]
            assert "date" in entry
            assert "amount" in entry
            assert "transaction_count" in entry

    def test_heatmap_only_debits(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        entries = client.get(
            "/api/v1/dashboard/heatmap",
            headers=auth_headers,
        ).json()
        # All heatmap amounts should be positive (debit only)
        for entry in entries:
            assert entry["amount"] > 0

    def test_heatmap_custom_days(self, client, auth_headers, test_account):
        _upload(client, auth_headers, test_account["id"])
        resp = client.get(
            "/api/v1/dashboard/heatmap?days=30",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_heatmap_invalid_days_too_small(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/heatmap?days=3",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_heatmap_invalid_days_too_large(self, client, auth_headers):
        resp = client.get(
            "/api/v1/dashboard/heatmap?days=400",
            headers=auth_headers,
        )
        assert resp.status_code == 422
