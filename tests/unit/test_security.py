"""
tests/unit/test_security.py
─────────────────────────────────────────────────────────────
Unit tests for backend/core/security.py

Tests cover:
  - PIN hashing and verification
  - PIN format validation
  - Session token generation and verification
  - Token expiry and tampering detection
  - File upload validation
  - CSV injection defense
  - Transaction hash determinism
  - File hash computation
  - Phone number validation
  - Input sanitization
"""

from __future__ import annotations

import hashlib
import time

import pytest

from backend.core.security import (
    compute_file_hash,
    compute_transaction_hash,
    hash_pin,
    sanitize_csv_cell,
    sanitize_string,
    validate_phone_number,
    validate_upload_file,
    verify_pin,
    generate_session_token,
    verify_session_token,
)


# ── PIN Tests ─────────────────────────────────────────────────────

class TestPinHashing:

    def test_hash_pin_returns_string(self):
        result = hash_pin("1234")
        assert isinstance(result, str)

    def test_hash_pin_length(self):
        result = hash_pin("1234")
        # bcrypt hashes are always 60 chars
        assert len(result) == 60

    def test_hash_pin_different_salts(self):
        h1 = hash_pin("1234")
        h2 = hash_pin("1234")
        # Same PIN, different salts — hashes must differ
        assert h1 != h2

    def test_verify_pin_correct(self):
        pin = "5678"
        hashed = hash_pin(pin)
        assert verify_pin(pin, hashed) is True

    def test_verify_pin_incorrect(self):
        hashed = hash_pin("1234")
        assert verify_pin("9999", hashed) is False

    def test_verify_pin_wrong_type(self):
        hashed = hash_pin("1234")
        # Should return False, not raise
        assert verify_pin("abcd", hashed) is False

    def test_hash_pin_rejects_non_digits(self):
        with pytest.raises(ValueError, match="digits only"):
            hash_pin("abcd")

    def test_hash_pin_rejects_too_short(self):
        with pytest.raises(ValueError, match="at least"):
            hash_pin("123")

    def test_hash_pin_rejects_too_long(self):
        with pytest.raises(ValueError, match="at most"):
            hash_pin("123456789")

    def test_hash_pin_accepts_max_length(self):
        result = hash_pin("12345678")
        assert verify_pin("12345678", result) is True

    def test_verify_pin_empty_string(self):
        hashed = hash_pin("1234")
        assert verify_pin("", hashed) is False

    def test_verify_pin_bad_hash(self):
        # Should return False, not raise
        assert verify_pin("1234", "not_a_valid_hash") is False


# ── Session Token Tests ───────────────────────────────────────────

class TestSessionTokens:

    def test_generate_token_returns_string(self):
        token = generate_session_token(user_id=1)
        assert isinstance(token, str)

    def test_generate_token_has_four_parts(self):
        token = generate_session_token(user_id=1)
        parts = token.split(".")
        assert len(parts) == 4

    def test_verify_valid_token(self):
        token = generate_session_token(user_id=42)
        payload = verify_session_token(token)
        assert payload is not None
        assert payload["user_id"] == 42
        assert payload["is_guest"] is False

    def test_verify_guest_token(self):
        token = generate_session_token(user_id=None, is_guest=True)
        payload = verify_session_token(token)
        assert payload is not None
        assert payload["user_id"] is None
        assert payload["is_guest"] is True

    def test_verify_tampered_token(self):
        token = generate_session_token(user_id=1)
        parts = token.split(".")
        # Tamper with user ID
        parts[1] = "999"
        tampered = ".".join(parts)
        result = verify_session_token(tampered)
        assert result is None

    def test_verify_invalid_token(self):
        assert verify_session_token("not.a.valid.token.at.all") is None

    def test_verify_empty_token(self):
        assert verify_session_token("") is None

    def test_verify_malformed_token(self):
        assert verify_session_token("abc.def") is None

    def test_different_users_get_different_tokens(self):
        t1 = generate_session_token(user_id=1)
        t2 = generate_session_token(user_id=2)
        assert t1 != t2

    def test_same_user_gets_different_tokens(self):
        # Random part ensures uniqueness
        t1 = generate_session_token(user_id=1)
        t2 = generate_session_token(user_id=1)
        assert t1 != t2


# ── File Upload Validation Tests ──────────────────────────────────

class TestFileUploadValidation:

    def test_valid_csv_file(self):
        valid, msg = validate_upload_file(
            filename="statement.csv",
            file_content=b"Date,Amount,Description",
            file_size=100,
        )
        assert valid is True
        assert msg == ""

    def test_valid_xlsx_file(self):
        # XLSX magic bytes: PK\x03\x04
        magic = b"\x50\x4b\x03\x04" + b"\x00" * 4
        valid, msg = validate_upload_file(
            filename="statement.xlsx",
            file_content=magic,
            file_size=1000,
        )
        assert valid is True

    def test_valid_pdf_file(self):
        # PDF magic bytes: %PDF
        magic = b"\x25\x50\x44\x46" + b"\x00" * 4
        valid, msg = validate_upload_file(
            filename="statement.pdf",
            file_content=magic,
            file_size=1000,
        )
        assert valid is True

    def test_invalid_extension(self):
        valid, msg = validate_upload_file(
            filename="virus.exe",
            file_content=b"\x4d\x5a",
            file_size=100,
        )
        assert valid is False
        assert "not allowed" in msg

    def test_file_too_large(self):
        valid, msg = validate_upload_file(
            filename="big.csv",
            file_content=b"data",
            file_size=11 * 1024 * 1024,  # 11MB > 10MB limit
        )
        assert valid is False
        assert "exceeds" in msg

    def test_path_traversal_blocked(self):
        valid, msg = validate_upload_file(
            filename="../../../etc/passwd",
            file_content=b"data",
            file_size=100,
        )
        assert valid is False

    def test_wrong_magic_bytes_xlsx(self):
        valid, msg = validate_upload_file(
            filename="fake.xlsx",
            file_content=b"This is not a ZIP file",
            file_size=100,
        )
        assert valid is False
        assert "content does not match" in msg

    def test_zip_disguised_as_pdf(self):
        # ZIP magic bytes but .pdf extension
        zip_magic = b"\x50\x4b\x03\x04"
        valid, msg = validate_upload_file(
            filename="evil.pdf",
            file_content=zip_magic,
            file_size=100,
        )
        assert valid is False


# ── CSV Injection Tests ───────────────────────────────────────────

class TestCSVInjection:

    def test_safe_value_unchanged(self):
        assert sanitize_csv_cell("Swiggy Order") == "Swiggy Order"

    def test_equals_prefix_neutralized(self):
        result = sanitize_csv_cell("=SUM(A1:A10)")
        assert result.startswith("'")
        assert "=SUM" in result

    def test_plus_prefix_neutralized(self):
        result = sanitize_csv_cell("+CMD")
        assert result.startswith("'")

    def test_minus_prefix_neutralized(self):
        result = sanitize_csv_cell("-2+3")
        assert result.startswith("'")

    def test_at_prefix_neutralized(self):
        result = sanitize_csv_cell("@SUM")
        assert result.startswith("'")

    def test_empty_string_safe(self):
        assert sanitize_csv_cell("") == ""

    def test_normal_number_safe(self):
        assert sanitize_csv_cell("1234.56") == "1234.56"


# ── Transaction Hash Tests ────────────────────────────────────────

class TestTransactionHash:

    def test_hash_returns_64_chars(self):
        h = compute_transaction_hash(
            account_id=1,
            date="2024-01-15",
            amount=500.0,
            description="Swiggy order",
            transaction_type="debit",
        )
        assert len(h) == 64

    def test_hash_is_deterministic(self):
        kwargs = dict(
            account_id=1,
            date="2024-01-15",
            amount=500.0,
            description="Swiggy order",
            transaction_type="debit",
        )
        h1 = compute_transaction_hash(**kwargs)
        h2 = compute_transaction_hash(**kwargs)
        assert h1 == h2

    def test_different_account_different_hash(self):
        h1 = compute_transaction_hash(1, "2024-01-15", 500.0, "Swiggy", "debit")
        h2 = compute_transaction_hash(2, "2024-01-15", 500.0, "Swiggy", "debit")
        assert h1 != h2

    def test_different_amount_different_hash(self):
        h1 = compute_transaction_hash(1, "2024-01-15", 500.0, "Swiggy", "debit")
        h2 = compute_transaction_hash(1, "2024-01-15", 600.0, "Swiggy", "debit")
        assert h1 != h2

    def test_different_date_different_hash(self):
        h1 = compute_transaction_hash(1, "2024-01-15", 500.0, "Swiggy", "debit")
        h2 = compute_transaction_hash(1, "2024-01-16", 500.0, "Swiggy", "debit")
        assert h1 != h2

    def test_description_case_insensitive(self):
        h1 = compute_transaction_hash(1, "2024-01-15", 500.0, "SWIGGY", "debit")
        h2 = compute_transaction_hash(1, "2024-01-15", 500.0, "swiggy", "debit")
        assert h1 == h2

    def test_description_strips_whitespace(self):
        h1 = compute_transaction_hash(1, "2024-01-15", 500.0, "swiggy", "debit")
        h2 = compute_transaction_hash(1, "2024-01-15", 500.0, "  swiggy  ", "debit")
        assert h1 == h2


# ── File Hash Tests ───────────────────────────────────────────────

class TestFileHash:

    def test_hash_returns_64_chars(self):
        h = compute_file_hash(b"some file content")
        assert len(h) == 64

    def test_same_content_same_hash(self):
        content = b"bank statement data"
        assert compute_file_hash(content) == compute_file_hash(content)

    def test_different_content_different_hash(self):
        h1 = compute_file_hash(b"content one")
        h2 = compute_file_hash(b"content two")
        assert h1 != h2

    def test_empty_bytes_hashes(self):
        h = compute_file_hash(b"")
        assert len(h) == 64


# ── Phone Validation Tests ────────────────────────────────────────

class TestPhoneValidation:

    def test_ten_digit_mobile(self):
        valid, normalized = validate_phone_number("9876543210")
        assert valid is True
        assert normalized == "+919876543210"

    def test_plus_91_format(self):
        valid, normalized = validate_phone_number("+919876543210")
        assert valid is True
        assert normalized == "+919876543210"

    def test_91_prefix_format(self):
        valid, normalized = validate_phone_number("919876543210")
        assert valid is True
        assert normalized == "+919876543210"

    def test_invalid_landline(self):
        valid, _ = validate_phone_number("0112345678")
        assert valid is False

    def test_invalid_short_number(self):
        valid, _ = validate_phone_number("12345")
        assert valid is False

    def test_invalid_letters(self):
        valid, _ = validate_phone_number("abcdefghij")
        assert valid is False

    def test_spaces_stripped(self):
        valid, normalized = validate_phone_number("98765 43210")
        assert valid is True
        assert normalized == "+919876543210"


# ── Sanitize String Tests ─────────────────────────────────────────

class TestSanitizeString:

    def test_strips_whitespace(self):
        assert sanitize_string("  hello  ") == "hello"

    def test_removes_null_bytes(self):
        assert sanitize_string("hel\x00lo") == "hello"

    def test_truncates_to_max_length(self):
        long_str = "a" * 600
        result = sanitize_string(long_str, max_length=500)
        assert len(result) == 500

    def test_non_string_returns_empty(self):
        assert sanitize_string(None) == ""
        assert sanitize_string(123) == ""

    def test_normal_string_unchanged(self):
        assert sanitize_string("Finance AI") == "Finance AI"
