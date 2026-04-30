"""
backend/models/__init__.py
─────────────────────────────────────────────────────────────
Single import point for all ORM models.

Importing all models here ensures SQLAlchemy's metadata
knows about every table before init_db() calls
Base.metadata.create_all().

Usage anywhere in the codebase:
    from backend.models import User, Account, Transaction
"""

from backend.models.user import User
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.upload_log import UploadLog
from backend.models.reminder import Reminder
from backend.models.insight import Insight
from backend.models.category import Category
from backend.models.session import UserSession

__all__ = [
    "User",
    "Account",
    "Transaction",
    "UploadLog",
    "Reminder",
    "Insight",
    "Category",
    "UserSession",
]
