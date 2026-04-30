"""
backend/models/category.py
─────────────────────────────────────────────────────────────
Category model — transaction category taxonomy.

Two types:
  is_system=True  — built-in categories seeded at startup.
                    Cannot be deleted by users.
  is_system=False — user-defined custom categories.

parent_category links subcategories to their parent.
Example:
  Food (parent)
    └── Swiggy (child, parent_category="Food")
    └── Zomato (child, parent_category="Food")
    └── Groceries (child, parent_category="Food")
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class Category(Base):
    __tablename__ = "categories"

    # ── Primary Key ───────────────────────────────────────────────
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # ── Category Info ─────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    parent_category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )

    # ── UI Hints ──────────────────────────────────────────────────
    icon: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    color: Mapped[Optional[str]] = mapped_column(
        String(7), nullable=True
    )

    # ── Type ──────────────────────────────────────────────────────
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Category name={self.name} "
            f"parent={self.parent_category}>"
        )
