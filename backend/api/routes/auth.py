"""
backend/api/routes/auth.py
Authentication endpoints.
"""

from __future__ import annotations
import ipaddress
from pydantic import BaseModel, Field, field_validator

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.core.database import get_db, wipe_guest_db
from backend.core.logger import get_logger
from backend.core.security import (
    generate_session_token,
    hash_pin,
    sanitize_string,
    validate_phone_number,
    verify_pin,
    verify_session_token,
)
from backend.models.session import UserSession
from backend.models.user import User

log = get_logger(__name__)
router = APIRouter()

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 30


# ============================================================
# Schemas
# ============================================================

class RegisterRequest(BaseModel):
    phone_number: str = Field(..., min_length=10, max_length=20)
    pin: str = Field(..., min_length=4, max_length=8)
    display_name: Optional[str] = Field(None, max_length=100)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str):
        valid, normalized = validate_phone_number(v)
        if not valid:
            raise ValueError("Invalid phone number.")
        return normalized

    @field_validator("pin")
    @classmethod
    def validate_pin(cls, v: str):
        if not v.isdigit():
            raise ValueError("PIN must contain digits.")
        return v


class LoginRequest(BaseModel):
    phone_number: str
    pin: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str):

        valid, normalized = validate_phone_number(v)

        if not valid:
            raise ValueError("Invalid phone number format.")

        return normalized


class ChangePinRequest(BaseModel):
    """
    Request model for changing user PIN.

    Security protections:
    - Enforces PIN length limits
    - Allows digits only
    - Prevents oversized payload attacks
    """

    current_pin: str = Field(
        ...,
        min_length=4,
        max_length=8,
        description="Current PIN"
    )

    new_pin: str = Field(
        ...,
        min_length=4,
        max_length=8,
        description="New PIN"
    )

    confirm_pin: str = Field(
        ...,
        min_length=4,
        max_length=8,
        description="Confirm new PIN"
    )

    @field_validator(
        "current_pin",
        "new_pin",
        "confirm_pin"
    )
    @classmethod
    def validate_digits_only(
        cls,
        value: str,
    ) -> str:
        if not value.isdigit():
            raise ValueError(
                "PIN must contain digits only."
            )

        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_guest: bool
    user_id: Optional[int]
    display_name: Optional[str]
    expires_in_hours: int


class UserInfoResponse(BaseModel):
    id: int
    phone_number: str
    display_name: Optional[str]
    created_at: datetime
    reminder_frequency: str
    currency: str


# ============================================================
# Dependency
# ============================================================

def get_current_user(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Session = Depends(get_db),
):

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    token = authorization.removeprefix("Bearer ").strip()

    payload = verify_session_token(token)

    if not payload or payload.get("is_guest"):
        raise HTTPException(
            status_code=401,
            detail="Invalid session",
        )

    user = db.query(User).filter(
        User.id == payload["user_id"],
        User.is_active == True,
    ).first()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    now = datetime.now(timezone.utc)

    # Normalize timezone for backward compatibility
    if user.locked_until:
        if user.locked_until.tzinfo is None:
            user.locked_until = user.locked_until.replace(tzinfo=timezone.utc)

    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=423,
            detail="Account locked",
        )

    return user


def get_current_user_or_guest(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Session = Depends(get_db),
):
    """
    FastAPI dependency that supports BOTH registered and guest sessions.

    - Registered users: validates token, fetches user from persistent DB.
    - Guest users: validates token, creates/reuses a synthetic guest user
      in the persistent DB (with a sentinel phone number) so that all
      downstream routes (accounts, transactions, etc.) work identically.

    The guest user is identified by phone_number = '__guest__'.
    Guest data is ephemeral — the guest DB is wiped on session end.
    """
    from backend.core.database import get_guest_db

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    token = authorization.removeprefix("Bearer ").strip()
    payload = verify_session_token(token)

    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid session",
        )

    # ── Guest session ────────────────────────────────────────────
    if payload.get("is_guest"):
        # For guest users, we create a synthetic user in the registered DB
        # with a sentinel phone number. This allows all routes to work
        # without modification since they all query by user_id.
        guest_user = db.query(User).filter(
            User.phone_number == "__guest__",
        ).first()

        if not guest_user:
            guest_user = User(
                phone_number="__guest__",
                pin_hash="guest_no_pin",
                display_name="Guest",
                is_active=True,
            )
            db.add(guest_user)
            db.flush()

        return guest_user

    # ── Registered session ───────────────────────────────────────
    user = db.query(User).filter(
        User.id == payload["user_id"],
        User.is_active == True,
    ).first()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    now = datetime.now(timezone.utc)

    if user.locked_until:
        if user.locked_until.tzinfo is None:
            user.locked_until = user.locked_until.replace(tzinfo=timezone.utc)

    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=423,
            detail="Account locked",
        )

    return user



# ============================================================
# Register
# ============================================================

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
)
async def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
):

    existing = db.query(User).filter(
        User.phone_number == body.phone_number
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Account already exists",
        )

    user = User(
        phone_number=body.phone_number,
        pin_hash=hash_pin(body.pin),
        display_name=(
            sanitize_string(body.display_name)
            if body.display_name
            else None
        ),
    )

    db.add(user)
    db.flush()

    token = generate_session_token(
        user.id,
        is_guest=False,
    )

    _persist_session(
        db,
        user.id,
        token,
        False,
        _get_client_ip(request),
    )

    db.commit()

    return TokenResponse(
        access_token=token,
        is_guest=False,
        user_id=user.id,
        display_name=user.display_name,
        expires_in_hours=24,
    )


# ============================================================
# Login
# ============================================================

@router.post(
    "/login",
    response_model=TokenResponse,
)
async def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):

    user = db.query(User).filter(
        User.phone_number == body.phone_number,
        User.is_active == True,
    ).first()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid phone or PIN",
        )

    now = datetime.now(timezone.utc)

    # Normalize timezone for backward compatibility
    if user.locked_until:
        if user.locked_until.tzinfo is None:
            user.locked_until = user.locked_until.replace(tzinfo=timezone.utc)

    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=423,
            detail="Account locked",
        )

    if not verify_pin(
        body.pin,
        user.pin_hash,
    ):

        user.failed_pin_attempts += 1

        if user.failed_pin_attempts >= MAX_FAILED_ATTEMPTS:

            user.locked_until = (
                datetime.now(timezone.utc)
                + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            )

        db.commit()

        raise HTTPException(
            status_code=401,
            detail="Invalid phone or PIN",
        )

    user.failed_pin_attempts = 0
    user.locked_until = None

    token = generate_session_token(
        user.id,
        is_guest=False,
    )

    _persist_session(
        db,
        user.id,
        token,
        False,
        _get_client_ip(request),
    )

    db.commit()

    return TokenResponse(
        access_token=token,
        is_guest=False,
        user_id=user.id,
        display_name=user.display_name,
        expires_in_hours=24,
    )


# ============================================================
# Guest Session
# ============================================================

@router.post(
    "/guest",
    response_model=TokenResponse,
)
async def create_guest_session(
    request: Request,
    db: Session = Depends(get_db),
):

    token = generate_session_token(
        user_id=None,
        is_guest=True,
    )

    _persist_session(
        db,
        user_id=None,
        token=token,
        is_guest=True,
        ip=_get_client_ip(request),
    )

    db.commit()

    return TokenResponse(
        access_token=token,
        is_guest=True,
        user_id=None,
        display_name="Guest",
        expires_in_hours=24,
    )


# ============================================================
# Logout
# ============================================================

@router.post(
    "/logout",
    status_code=204,
    response_model=None,
)
async def logout(
    authorization: Annotated[Optional[str], Header()] = None,
    db: Session = Depends(get_db),
):

    if authorization and authorization.startswith("Bearer "):

        token = authorization.removeprefix("Bearer ").strip()

        payload = verify_session_token(token)

        if payload:

            token_hash = hashlib.sha256(
                token.encode()
            ).hexdigest()

            session = db.query(UserSession).filter(
                UserSession.token_hash == token_hash,
                UserSession.is_active == True,
            ).first()

            if session:
                session.is_active = False
                db.commit()

            if payload.get("is_guest"):
                wipe_guest_db()

    return Response(status_code=204)


# ============================================================
# Change PIN
# ============================================================

@router.post(
    "/pin/change",
    status_code=204,
    response_model=None,
)
async def change_pin(
    body: ChangePinRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if body.new_pin != body.confirm_pin:
        raise HTTPException(
            status_code=422,
            detail="PIN mismatch",
        )

    if not verify_pin(
        body.current_pin,
        current_user.pin_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Current PIN incorrect",
        )

    current_user.pin_hash = hash_pin(
        body.new_pin,
    )

    # Invalidate all existing sessions on PIN change
    # Forces re-login — revokes any stolen tokens
    db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.is_active == True,
    ).update({"is_active": False}, synchronize_session=False)

    db.commit()

    return Response(status_code=204)


# ============================================================
# Get Me
# ============================================================

@router.get(
    "/me",
    response_model=UserInfoResponse,
)
async def get_me(
    current_user: User = Depends(get_current_user),
):

    return UserInfoResponse(
        id=current_user.id,
        phone_number=current_user.phone_number,
        display_name=current_user.display_name,
        created_at=current_user.created_at,
        reminder_frequency=current_user.reminder_frequency,
        currency=current_user.currency,
    )


# ============================================================
# Helpers
# ============================================================

def _persist_session(
    db: Session,
    user_id,
    token,
    is_guest,
    ip,
):

    token_hash = hashlib.sha256(
        token.encode()
    ).hexdigest()

    session = UserSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        is_guest=is_guest,
        token_hash=token_hash,
        expires_at=(
    datetime.now(timezone.utc)
    + timedelta(hours=24)
),
        ip_address=ip,
    )

    db.add(session)


def _get_client_ip(
    request: Request,
) -> str | None:
    """
    Securely extract client IP address.

    Security protections:
    - Validates IP format using ipaddress module
    - Prevents header injection
    - Limits length to safe IPv6 size
    - Never stores raw header values
    """

    MAX_IP_LENGTH = 45  # max IPv6 string length

    def _sanitize_ip(raw: str) -> str | None:
        try:
            ip = raw.split(",")[0].strip()[:MAX_IP_LENGTH]

            parsed = ipaddress.ip_address(ip)

            return str(parsed)

        except ValueError:
            return None

    forwarded = request.headers.get(
        "X-Forwarded-For"
    )

    if forwarded:
        ip = _sanitize_ip(forwarded)

        if ip:
            return ip

    if request.client:
        try:
            parsed = ipaddress.ip_address(
                request.client.host
            )
            return str(parsed)

        except ValueError:
            return None

    return None