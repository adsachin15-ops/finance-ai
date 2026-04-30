"""
backend/models/session.py
─────────────────────────────────────────────────────────────
UserSession model — tracks active login sessions.

Design:
  - id is a UUID string (not integer) for unguessability.
  - token_hash stores SHA-256 of the raw token, not the token itself.
    If DB is compromised, raw tokens cannot be recovered.
  - user_id is NULL for guest sessions.
  - is_active flag allows server-side session invalidation.
  - ip_address stored for audit trail only.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User


class UserSession(Base):
    __tablename__ = "user_sessions"

    # ── Primary Key ───────────────────────────────────────────────
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True
    )

    # ── Foreign Key ───────────────────────────────────────────────
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Session Info ──────────────────────────────────────────────
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── Timestamps ────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── State ─────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True
    )

    # ── Relationship ──────────────────────────────────────────────
    user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="sessions"
    )

    def __repr__(self) -> str:
        return (
            f"<UserSession id={self.id} "
            f"guest={self.is_guest} "
            f"active={self.is_active}>"
        )
