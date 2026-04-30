"""Add user preferences fields

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00.000000

Adds new columns to users table:
  - timezone         → user's local timezone (Phase 2 reminders)
  - monthly_budget   → optional spending budget for alerts
  - onboarding_done  → tracks if user completed setup flow

Demonstrates safe column addition on SQLite using
batch_alter_table. Existing rows get the server_default
value automatically — no data migration needed.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add preference columns to users table."""

    with op.batch_alter_table("users") as batch_op:

        batch_op.add_column(
            sa.Column(
                "timezone",
                sa.String(50),
                nullable=False,
                server_default="Asia/Kolkata",
            )
        )

        batch_op.add_column(
            sa.Column(
                "monthly_budget",
                sa.Float(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "onboarding_done",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    """Remove preference columns from users table."""

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("onboarding_done")
        batch_op.drop_column("monthly_budget")
        batch_op.drop_column("timezone")
