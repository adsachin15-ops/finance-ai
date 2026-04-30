"""
backend/models/insight.py
─────────────────────────────────────────────────────────────
Insight model — AI-generated financial insights stored per user.

Insight types:
  spending_trend  — "You spent 18% more on food this week"
  anomaly         — "Unusual transaction detected: ₹45,000 at 2am"
  prediction      — "Estimated spend this month: ₹24,500"
  health_score    — "Your financial health score is 74/100"
  budget_alert    — "You have used 90% of your food budget"

Severity levels:
  info    — neutral observation
  warning — requires attention
  alert   — immediate action recommended
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User


class Insight(Base):
    __tablename__ = "insights"

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

    # ── Insight Content ───────────────────────────────────────────
    insight_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(10), default="info", nullable=False
    )

    # ── Period ────────────────────────────────────────────────────
    period_start: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    period_end: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )

    # ── State ─────────────────────────────────────────────────────
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Relationship ──────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User", back_populates="insights"
    )

    def __repr__(self) -> str:
        return (
            f"<Insight id={self.id} "
            f"type={self.insight_type} "
            f"severity={self.severity}>"
        )
