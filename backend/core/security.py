from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt

from backend.core.config import get_settings
from backend.core.logger import get_logger

log = get_logger(__name__)
settings = get_settings()


# ============================================================
# PIN Security
# ============================================================

def hash_pin(pin: str) -> str:

    _validate_pin_format(pin)

    salt = bcrypt.gensalt(rounds=12)

    hashed = bcrypt.hashpw(
        pin.encode("utf-8"),
        salt,
    )

    return hashed.decode("utf-8")


def verify_pin(
    pin: str,
    pin_hash: str,
) -> bool:

    try:

        return bcrypt.checkpw(
            pin.encode("utf-8"),
            pin_hash.encode("utf-8"),
        )

    except Exception:
        # Do not log exception detail — may contain pin data
        log.warning("pin.verify.error")
        return False


def _validate_pin_format(pin: str) -> None:

    if not pin.isdigit():

        raise ValueError(
            "PIN must contain digits only."
        )

    if len(pin) < settings.pin_min_length:

        raise ValueError(
            f"PIN must be at least "
            f"{settings.pin_min_length} digits."
        )

    if len(pin) > settings.pin_max_length:

        raise ValueError(
            f"PIN must be at most "
            f"{settings.pin_max_length} digits."
        )


# ============================================================
# Session Tokens
# ============================================================

def generate_session_token(
    user_id: Optional[int],
    is_guest: bool = False,
) -> str:

    random_part = secrets.token_hex(32)

    uid = "guest" if is_guest else str(user_id)

    expiry = int(
        (
            datetime.now(timezone.utc)
            + timedelta(
                hours=settings.session_expire_hours
            )
        ).timestamp()
    )

    payload = f"{random_part}.{uid}.{expiry}"

    signature = _sign(payload)

    return f"{payload}.{signature}"


def verify_session_token(
    token: str,
) -> Optional[dict]:

    try:

        parts = token.split(".")

        if len(parts) != 4:

            return None

        random_part, uid, expiry_str, received_sig = parts

        payload = f"{random_part}.{uid}.{expiry_str}"

        expected_sig = _sign(payload)

        if not hmac.compare_digest(
            expected_sig,
            received_sig,
        ):

            return None

        expiry = int(expiry_str)

        if (
            datetime.now(timezone.utc)
            .timestamp()
            > expiry
        ):

            return None

        return {
            "user_id":
                None
                if uid == "guest"
                else int(uid),
            "is_guest":
                uid == "guest",
            "expires_at":
                datetime.fromtimestamp(
                    expiry,
                    tz=timezone.utc,
                ),
        }

    except Exception:
        # Do not log exception detail — may contain token fragments
        log.warning("session.token.verify.error")
        return None


def _sign(payload: str) -> str:

    return hmac.HMAC(
        settings.secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ============================================================
# Phone Validation
# ============================================================

def validate_phone_number(
    phone: str,
) -> tuple[bool, str]:

    if not phone:
        return False, ""

    cleaned = re.sub(
        r"[\s\-()]",
        "",
        phone,
    )

    digits = cleaned.replace("+", "")

    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]

    if re.match(r"^[6-9]\d{9}$", digits):

        return True, f"+91{digits}"

    return False, ""


# ============================================================
# Input Sanitization
# ============================================================

def sanitize_string(
    value: str,
    max_length: int = 500,
) -> str:

    if not isinstance(value, str):

        return ""

    cleaned = value.strip()

    cleaned = cleaned.replace(
        "\x00",
        "",
    )

    return cleaned[:max_length]


# ============================================================
# File Hashing
# ============================================================

def compute_file_hash(
    file_bytes: bytes,
) -> str:

    if not isinstance(
        file_bytes,
        (bytes, bytearray),
    ):

        raise ValueError(
            "file_bytes must be bytes"
        )

    return hashlib.sha256(
        file_bytes
    ).hexdigest()


def compute_transaction_hash(
    account_id: int,
    date: str,
    amount: float,
    description: str,
    transaction_type: str,
) -> str:

    normalized = (
        f"{account_id}"
        f"|{date}"
        f"|{round(amount, 2):.2f}"
        f"|{description.lower().strip()}"
        f"|{transaction_type.lower()}"
    )

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


# ============================================================
# Upload Validation
# ============================================================

def validate_upload_file(
    filename: str,
    file_content: bytes,
    file_size: int,
) -> tuple[bool, str]:

    ext = (
        Path(filename)
        .suffix
        .lstrip(".")
        .lower()
    )

    # ── Extension check ─────────────────────────
    if ext not in settings.allowed_ext_set:
        return (
            False,
            f"File type '.{ext}' not allowed.",
        )

    # ── File size check ─────────────────────────
    if file_size > settings.max_file_size_bytes:
        return (
            False,
            (
                f"File size "
                f"{file_size / (1024*1024):.1f}MB "
                f"exceeds limit of "
                f"{settings.max_file_size_mb}MB."
            ),
        )

    # ── ✅ ADD THIS BLOCK HERE ───────────────────
    MAGIC_BYTES = {
        "pdf": b"%PDF",
    }

    if ext in MAGIC_BYTES:
        expected = MAGIC_BYTES[ext]

        if not file_content.startswith(expected):
            return False, (
                f"File content does not match expected format for .{ext}"
            )

    # ── Path traversal protection ───────────────
    dangerous = (
        "..",
        "/",
        "\\",
        ":",
        "*",
        "?",
        '"',
        "<",
        ">",
        "|",
    )

    if any(d in filename for d in dangerous):
        return False, "Invalid filename."

    return True, ""


# ============================================================
# CSV Injection Protection
# ============================================================

_CSV_INJECTION_PREFIXES = (
    "=",
    "+",
    "-",
    "@",
    "\t",
    "\r",
)


def sanitize_csv_cell(
    value: str,
) -> str:

    if (
        isinstance(value, str)
        and value.startswith(
            _CSV_INJECTION_PREFIXES
        )
    ):

        return "'" + value

    return value