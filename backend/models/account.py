"""
backend/models/account.py
─────────────────────────────────────────────────────────────
Account model — financial accounts owned by a user.

Account types:
  savings     — bank savings account
  credit_card — credit card (has credit_limit)
  wallet      — digital wallet (Paytm, PhonePe)
  upi         — UPI-linked account
  cash        — physical cash tracking
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User
    from backend.models.transaction import Transaction
    from backend.models.upload_log import UploadLog


class Account(Base):
    __tablename__ = "accounts"

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

    # ── Identity ──────────────────────────────────────────────────
    nickname: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    bank_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    account_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    last_four_digits: Mapped[Optional[str]] = mapped_column(
        String(4), nullable=True
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", nullable=False
    )

    # ── Balances ──────────────────────────────────────────────────
    current_balance: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    credit_limit: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    # ── State ─────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User", back_populates="accounts"
    )
    transactions: Mapped[List["Transaction"]] = relationship(
        "Transaction",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    upload_logs: Mapped[List["UploadLog"]] = relationship(
        "UploadLog", back_populates="account"
    )

    # ── Properties ────────────────────────────────────────────────

    @property
    def credit_utilization(self) -> Optional[float]:
        if (
            self.account_type == "credit_card"
            and self.credit_limit
            and self.credit_limit > 0
        ):
            used = self.credit_limit - self.current_balance
            return round((used / self.credit_limit) * 100, 2)
        return None

    def __repr__(self) -> str:
        return (
            f"<Account id={self.id} "
            f"type={self.account_type} "
            f"nick={self.nickname}>"
        )
