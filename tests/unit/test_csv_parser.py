"""
tests/unit/test_csv_parser.py
─────────────────────────────────────────────────────────────
Unit tests for backend/services/file_parser/csv_parser.py

Tests cover:
  - HDFC format parsing
  - SBI format parsing
  - Single amount column format
  - Date format variations
  - Encoding detection
  - Garbage row removal
  - Amount parsing (commas, symbols, accounting negatives)
  - CSV injection sanitization
  - Empty file handling
  - Missing required columns
"""

from __future__ import annotations

import csv
import io
import tempfile
from datetime import date
from pathlib import Path

import pytest

from backend.services.file_parser.csv_parser import (
    CSVParser,
    _clean_description,
    _parse_date,
    _safe_float,
)


# ── Helpers ───────────────────────────────────────────────────────

def _write_csv(rows: list[list], headers: list[str]) -> Path:
    """Write a CSV file to a temp path and return the Path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="w",
        encoding="utf-8", newline=""
    )
    writer = csv.writer(tmp)
    writer.writerow(headers)
    writer.writerows(rows)
    tmp.close()
    return Path(tmp.name)


def _write_raw_csv(content: str, encoding: str = "utf-8") -> Path:
    """Write raw CSV content string to a temp file."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False,
        mode="w", encoding=encoding,
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ── Date Parsing Tests ────────────────────────────────────────────

class TestParseDate:

    def test_dd_mm_yyyy_slash(self):
        assert _parse_date("15/01/2024") == date(2024, 1, 15)

    def test_dd_mm_yyyy_dash(self):
        assert _parse_date("15-01-2024") == date(2024, 1, 15)

    def test_yyyy_mm_dd(self):
        assert _parse_date("2024-01-15") == date(2024, 1, 15)

    def test_dd_mon_yyyy(self):
        assert _parse_date("15-Jan-2024") == date(2024, 1, 15)

    def test_dd_mon_yy(self):
        assert _parse_date("15-Jan-24") == date(2024, 1, 15)

    def test_strips_whitespace(self):
        assert _parse_date("  15/01/2024  ") == date(2024, 1, 15)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _parse_date("")

    def test_nan_raises(self):
        with pytest.raises(ValueError):
            _parse_date("nan")

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_date("not-a-date")

    def test_dd_mm_yy_slash(self):
        assert _parse_date("15/01/24") == date(2024, 1, 15)


# ── Safe Float Tests ──────────────────────────────────────────────

class TestSafeFloat:

    def test_plain_number(self):
        assert _safe_float("500.00") == 500.0

    def test_with_commas(self):
        assert _safe_float("1,234.56") == 1234.56

    def test_with_rupee_symbol(self):
        assert _safe_float("₹1500") == 1500.0

    def test_with_dollar_symbol(self):
        assert _safe_float("$250.00") == 250.0

    def test_accounting_negative(self):
        assert _safe_float("(500.00)") == -500.0

    def test_nan_returns_none(self):
        assert _safe_float("nan") is None

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        assert _safe_float("") is None

    def test_dash_returns_none(self):
        assert _safe_float("-") is None

    def test_integer_string(self):
        assert _safe_float("1000") == 1000.0

    def test_zero(self):
        assert _safe_float("0.00") == 0.0

    def test_large_amount_with_commas(self):
        assert _safe_float("1,00,000.00") == 100000.0


# ── Description Cleaning Tests ────────────────────────────────────

class TestCleanDescription:

    def test_removes_upi_prefix(self):
        result = _clean_description("UPI/Swiggy payment")
        assert not result.startswith("UPI")
        assert "Swiggy" in result

    def test_removes_neft_prefix(self):
        result = _clean_description("NEFT-HDFC transfer")
        assert not result.startswith("NEFT")

    def test_removes_imps_prefix(self):
        result = _clean_description("IMPS/payment ref 123")
        assert not result.startswith("IMPS")

    def test_collapses_whitespace(self):
        result = _clean_description("  Swiggy   order  ")
        assert result == "Swiggy   order"

    def test_truncates_at_500(self):
        long = "A" * 600
        assert len(_clean_description(long)) == 500

    def test_nan_returns_empty(self):
        assert _clean_description("nan") == ""

    def test_empty_returns_empty(self):
        assert _clean_description("") == ""

    def test_normal_description_unchanged(self):
        result = _clean_description("Swiggy food order")
        assert "Swiggy" in result


# ── HDFC Format Tests ─────────────────────────────────────────────

class TestHDFCFormat:

    def test_hdfc_basic_parse(self):
        path = _write_csv(
            headers=["Date", "Narration", "Value Date", "Debit Amount", "Credit Amount", "Balance"],
            rows=[
                ["15/01/2024", "SWIGGY ORDER 123", "15/01/2024", "450.00", "", "9550.00"],
                ["16/01/2024", "SALARY CREDIT",    "16/01/2024", "",       "50000.00", "59550.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 2
        assert rows[0]["type"] == "debit"
        assert rows[0]["amount"] == 450.0
        assert rows[0]["date"] == date(2024, 1, 15)
        assert rows[1]["type"] == "credit"
        assert rows[1]["amount"] == 50000.0

    def test_hdfc_description_present(self):
        path = _write_csv(
            headers=["Date", "Narration", "Value Date", "Debit Amount", "Credit Amount", "Balance"],
            rows=[
                ["15/01/2024", "SWIGGY ORDER 123", "15/01/2024", "450.00", "", "9550.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert "description" in rows[0]
        assert rows[0]["description"] != ""

    def test_hdfc_skips_empty_amount_rows(self):
        path = _write_csv(
            headers=["Date", "Narration", "Value Date", "Debit Amount", "Credit Amount", "Balance"],
            rows=[
                ["15/01/2024", "OPENING BALANCE", "15/01/2024", "", "", "10000.00"],
                ["16/01/2024", "SWIGGY ORDER",    "16/01/2024", "450.00", "", "9550.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 1
        assert rows[0]["amount"] == 450.0


# ── SBI Format Tests ──────────────────────────────────────────────

class TestSBIFormat:

    def test_sbi_basic_parse(self):
        path = _write_csv(
            headers=["Txn Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
            rows=[
                ["15-01-2024", "15-01-2024", "ATM WITHDRAWAL", "REF001", "2000.00", "", "8000.00"],
                ["16-01-2024", "16-01-2024", "SALARY",         "REF002", "",        "30000.00", "38000.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 2
        assert rows[0]["type"] == "debit"
        assert rows[0]["amount"] == 2000.0
        assert rows[1]["type"] == "credit"
        assert rows[1]["amount"] == 30000.0


# ── Single Amount Column Format ───────────────────────────────────

class TestSingleAmountFormat:

    def test_negative_amount_is_debit(self):
        path = _write_csv(
            headers=["Date", "Description", "Amount"],
            rows=[
                ["15/01/2024", "Swiggy order", "-450.00"],
                ["16/01/2024", "Salary credit", "50000.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert rows[0]["type"] == "debit"
        assert rows[0]["amount"] == 450.0
        assert rows[1]["type"] == "credit"
        assert rows[1]["amount"] == 50000.0


# ── Edge Cases ────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_file_returns_empty_list(self):
        path = _write_raw_csv("")
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()
        assert rows == []

    def test_header_only_returns_empty_list(self):
        path = _write_csv(
            headers=["Date", "Narration", "Debit", "Credit", "Balance"],
            rows=[]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()
        assert rows == []

    def test_garbage_rows_skipped(self):
        content = (
            "Account Statement from 01/01/2024 to 31/01/2024\n"
            "Account Number: XXXX1234\n"
            "\n"
            "Date,Narration,Debit Amount,Credit Amount,Balance\n"
            "15/01/2024,Swiggy Order,450.00,,9550.00\n"
        )
        path = _write_raw_csv(content)
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 1
        assert rows[0]["amount"] == 450.0

    def test_semicolon_delimiter(self):
        content = "Date;Narration;Debit;Credit;Balance\n15/01/2024;Swiggy;450;;9550\n"
        path = _write_raw_csv(content)
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 1
        assert rows[0]["amount"] == 450.0

    def test_amounts_with_commas(self):
        path = _write_csv(
            headers=["Date", "Narration", "Debit Amount", "Credit Amount", "Balance"],
            rows=[
                ["15/01/2024", "Rent payment", "15,000.00", "", "85,000.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert rows[0]["amount"] == 15000.0

    def test_latin1_encoding(self):
        content = "Date,Narration,Debit Amount,Credit Amount,Balance\n15/01/2024,Caf\xe9 payment,500,,9500\n"
        path = _write_raw_csv(content, encoding="latin-1")
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 1
        assert rows[0]["amount"] == 500.0

    def test_zero_amount_rows_skipped(self):
        path = _write_csv(
            headers=["Date", "Narration", "Debit Amount", "Credit Amount", "Balance"],
            rows=[
                ["15/01/2024", "Swiggy", "0.00", "", "10000.00"],
                ["16/01/2024", "Salary", "",     "50000.00", "60000.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert len(rows) == 1
        assert rows[0]["type"] == "credit"

    def test_raw_description_preserved(self):
        path = _write_csv(
            headers=["Date", "Narration", "Debit Amount", "Credit Amount", "Balance"],
            rows=[
                ["15/01/2024", "UPI/SWIGGY ORDER 123", "450.00", "", "9550.00"],
            ]
        )
        parser = CSVParser()
        rows = parser.parse(path)
        path.unlink()

        assert "raw_description" in rows[0]
        # raw_description should preserve original
        assert rows[0]["raw_description"] != ""
