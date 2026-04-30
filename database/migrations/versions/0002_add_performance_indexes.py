"""Add performance indexes for dashboard queries

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000

Adds indexes optimized for:
  - Dashboard summary queries (date range scans)
  - Upload log lookups by file hash (duplicate detection)
  - Session lookups by token hash (auth)
  - Insight queries by read status

These indexes are additive — safe to apply on live DB
with existing data. SQLite builds them without locking.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add performance indexes."""

    with op.batch_alter_table("transactions") as batch_op:
        # Fast date range scans for dashboard periods
        batch_op.create_index(
            "ix_tx_date",
            ["date"],
        )

    with op.batch_alter_table("upload_logs") as batch_op:
        # Fast duplicate file detection by hash
        batch_op.create_index(
            "ix_upload_logs_file_hash",
            ["file_hash"],
        )

    with op.batch_alter_table("user_sessions") as batch_op:
        # Fast token lookup on every authenticated request
        batch_op.create_index(
            "ix_sessions_token_hash",
            ["token_hash"],
        )

    with op.batch_alter_table("insights") as batch_op:
        # Fast unread insight queries
        batch_op.create_index(
            "ix_insights_user_read",
            ["user_id", "is_read"],
        )


def downgrade() -> None:
    """Remove performance indexes."""

    with op.batch_alter_table("insights") as batch_op:
        batch_op.drop_index("ix_insights_user_read")

    with op.batch_alter_table("user_sessions") as batch_op:
        batch_op.drop_index("ix_sessions_token_hash")

    with op.batch_alter_table("upload_logs") as batch_op:
        batch_op.drop_index("ix_upload_logs_file_hash")

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index("ix_tx_date")
