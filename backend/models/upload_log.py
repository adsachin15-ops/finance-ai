"""
backend/models/upload_log.py
─────────────────────────────────────────────────────────────
UploadLog model — tracks every file upload attempt.

Records:
  - Which user uploaded which file
  - File hash (SHA-256) for duplicate file detection
  - Processing stats: parsed, inserted, duplicate, failed counts
  - Status: pending → processing → completed | failed
  - Error message if processing failed
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.user import User
    from backend.models.account import Account


class UploadLog(Base):
    __tablename__ = "upload_logs"

    # ── Primary Key ───────────────────────────────────────────────
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # ── Foreign Keys ──────────────────────────────────────────────
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("accounts.id"),
        nullable=True,
    )

    # ── File Info ─────────────────────────────────────────────────
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Timestamps ────────────────────────────────────────────────
    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Status ────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )

    # ── Processing Stats ──────────────────────────────────────────
    records_parsed: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────
    user: Mapped["User"] = relationship("User")
    account: Mapped[Optional["Account"]] = relationship(
        "Account", back_populates="upload_logs"
    )

    def __repr__(self) -> str:
        return (
            f"<UploadLog id={self.id} "
            f"file={self.file_name} "
            f"status={self.status}>"
        )
