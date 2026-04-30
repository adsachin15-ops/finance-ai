"""
backend/models/transaction.py
─────────────────────────────────────────────────────────────
Transaction model — core financial record.

Key design decisions:
  - hash (UNIQUE) → deduplication without SELECT-before-INSERT.
    Same transaction uploaded twice → IntegrityError on second insert.
    Service layer catches it and counts as duplicate.

  - raw_description → preserved from original file, immutable.
    description → cleaned display version, mutable.
    category → AI-assigned, mutable (user can correct it).

  - Composite indexes on (account_id, date) and (category) for
    fast dashboard queries at 50,000+ rows.

  - amount is always positive. type field (debit/credit) determines
    the direction of money flow.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date, DateTime, Float, ForeignKey,
    Index, Integer, String, Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.account import Account


class Transaction(Base):
    __tablename__ = "transactions"

    __table_args__ = (
        Index("ix_tx_account_date", "account_id", "date"),
        Index("ix_tx_category", "category"),
        Index("ix_tx_type_date", "type", "date"),
    )

    # ── Primary Key ───────────────────────────────────────────────
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # ── Foreign Key ───────────────────────────────────────────────
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Core Financial Data ───────────────────────────────────────
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False)

    # ── Categorization ────────────────────────────────────────────
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    subcategory: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    merchant: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # ── Description ───────────────────────────────────────────────
    description: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    raw_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    # ── Provenance ────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )

    # ── Notes ─────────────────────────────────────────────────────
    notes: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )

    # ── Timestamps ────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # ── Relationship ──────────────────────────────────────────────
    account: Mapped["Account"] = relationship(
        "Account", back_populates="transactions"
    )

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} "
            f"date={self.date} "
            f"amount={self.amount} "
            f"type={self.type} "
            f"cat={self.category}>"
        )
