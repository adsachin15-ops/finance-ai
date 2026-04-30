"""
backend/models/user.py
─────────────────────────────────────────────────────────────
User model — registered users who own accounts and transactions.

Design notes:
  - phone_number is the primary identifier in Phase 1.
  - pin_hash stores bcrypt hash. Raw PIN never touches the DB.
  - Multi-user from day one: all queries MUST filter by user_id.
  - Soft delete via is_active: never hard-delete a user's data.
  - Account lockout after 5 failed PIN attempts (30 min).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.account import Account
    from backend.models.reminder import Reminder
    from backend.models.insight import Insight
    from backend.models.session import UserSession


class User(Base):
    __tablename__ = "users"

    # ── Primary Key ───────────────────────────────────────────────
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # ── Identity ──────────────────────────────────────────────────
    phone_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    pin_hash: Mapped[str] = mapped_column(
        String(72), nullable=False
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # ── Timestamps ────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Preferences ───────────────────────────────────────────────
    reminder_frequency: Mapped[str] = mapped_column(
        String(20), default="weekly", nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", nullable=False
    )

    # ── State ─────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    failed_pin_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────────
    accounts: Mapped[List["Account"]] = relationship(
        "Account",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    reminders: Mapped[List["Reminder"]] = relationship(
        "Reminder",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    insights: Mapped[List["Insight"]] = relationship(
        "Insight",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    sessions: Mapped[List["UserSession"]] = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_locked(self) -> bool:
        """
        Check if account is currently locked.

        Handles both naive and timezone-aware datetimes safely.
        """

        if not self.locked_until:
            return False

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        # If stored datetime is naive, compare using naive time
        if self.locked_until.tzinfo is None:
            return datetime.utcnow() < self.locked_until

        return now < self.locked_until

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} "
            f"phone={self.phone_number} "
            f"active={self.is_active}>"
        )
