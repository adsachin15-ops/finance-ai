"""
backend/models/reminder.py
─────────────────────────────────────────────────────────────
Reminder model — scheduled notifications for users.

Types:
  weekly  — triggers on a specific day of week (0=Mon, 6=Sun)
  monthly — triggers on a specific day of month (1-31)
  custom  — user-defined schedule

Notification methods (Phase 1):
  Local only — printed to console / shown in UI.

Phase 2:
  Email via SMTP when cloud config is present.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User


class Reminder(Base):
    __tablename__ = "reminders"

    # ── Primary Key ───────────────────────────────────────────────
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # ── Foreign Key ───────────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Reminder Content ──────────────────────────────────────────
    reminder_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )

    # ── Schedule ──────────────────────────────────────────────────
    trigger_day: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    trigger_time: Mapped[str] = mapped_column(
        String(5), default="09:00", nullable=False
    )

    # ── State ─────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationship ──────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User", back_populates="reminders"
    )

    def __repr__(self) -> str:
        return (
            f"<Reminder id={self.id} "
            f"type={self.reminder_type} "
            f"active={self.is_active}>"
        )
