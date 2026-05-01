"""
backend/api/routes/accounts.py
─────────────────────────────────────────────────────────────
Account management endpoints.

GET    /accounts/           → list all user accounts
POST   /accounts/           → create account
GET    /accounts/{id}       → get single account
PUT    /accounts/{id}       → update account
DELETE /accounts/{id}       → soft-delete account
GET    /accounts/{id}/summary → balance + transaction summary
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_user_or_guest as get_current_user
from backend.core.database import get_db
from backend.core.logger import get_logger
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.user import User

log = get_logger(__name__)
router = APIRouter()

VALID_ACCOUNT_TYPES = {
    "savings", "wallet", "upi", "cash"
}


# ── Schemas ───────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=100)
    bank_name: Optional[str] = Field(None, max_length=100)
    account_type: str = Field(
        ..., description="savings | wallet | upi | cash"
    )
    last_four_digits: Optional[str] = Field(
        None, min_length=4, max_length=4, pattern=r"^\d{4}$"
    )
    currency: str = Field("INR", min_length=3, max_length=3)
    current_balance: float = Field(0.0)
    credit_limit: Optional[float] = Field(None)


class AccountUpdate(BaseModel):
    nickname: Optional[str] = Field(None, min_length=1, max_length=100)
    bank_name: Optional[str] = None
    current_balance: Optional[float] = None
    credit_limit: Optional[float] = None
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: int
    user_id: int
    nickname: str
    bank_name: Optional[str]
    account_type: str
    last_four_digits: Optional[str]
    currency: str
    current_balance: float
    credit_limit: Optional[float]
    credit_utilization: Optional[float]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountSummaryResponse(BaseModel):
    account_id: int
    nickname: str
    account_type: str
    current_balance: float
    total_transactions: int
    total_debits: float
    total_credits: float


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/", response_model=List[AccountResponse])
async def list_accounts(
    include_inactive: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[AccountResponse]:
    """List all accounts for the authenticated user."""
    query = db.query(Account).filter(
        Account.user_id == current_user.id
    )
    if not include_inactive:
        query = query.filter(Account.is_active == True)
    accounts = query.order_by(Account.created_at.asc()).all()
    return [AccountResponse.model_validate(a) for a in accounts]


@router.post(
    "/",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    body: AccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """Create a new financial account."""
    if body.account_type not in VALID_ACCOUNT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid account type. "
                f"Must be one of: {', '.join(VALID_ACCOUNT_TYPES)}"
            ),
        )

    existing = db.query(Account).filter(
        Account.user_id == current_user.id,
        Account.nickname == body.nickname,
        Account.is_active == True,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account named '{body.nickname}' already exists.",
        )

    # Sanitize string fields before DB write
    def _clean(v):
        return v.strip().replace("\x00", "")[:200] if v else None

    account = Account(
        user_id=current_user.id,
        nickname=_clean(body.nickname),
        bank_name=_clean(body.bank_name),
        account_type=body.account_type,
        last_four_digits=body.last_four_digits,
        currency=body.currency.upper(),
        current_balance=body.current_balance,
        credit_limit=None,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    log.info(
        "account.created",
        user_id=current_user.id,
        account_id=account.id,
        type=body.account_type,
    )
    return AccountResponse.model_validate(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """Get a single account by ID."""
    account = _get_user_account(account_id, current_user.id, db)
    return AccountResponse.model_validate(account)


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    body: AccountUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """Update account fields."""
    account = _get_user_account(account_id, current_user.id, db)
    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    log.info(
        "account.updated",
        account_id=account_id,
        fields=list(update_data.keys()),
    )
    return AccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Soft-delete an account (preserves transaction history)."""
    account = _get_user_account(account_id, current_user.id, db)
    account.is_active = False
    db.commit()
    log.info(
        "account.deactivated",
        account_id=account_id,
        user_id=current_user.id,
    )


@router.get("/{account_id}/summary", response_model=AccountSummaryResponse)
async def get_account_summary(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountSummaryResponse:
    """Return aggregated financial summary for a specific account."""
    account = _get_user_account(account_id, current_user.id, db)

    stats = db.query(
        func.count(Transaction.id).label("total"),
        func.sum(
            case((Transaction.type == "debit", Transaction.amount), else_=0)
        ).label("total_debits"),
        func.sum(
            case((Transaction.type == "credit", Transaction.amount), else_=0)
        ).label("total_credits"),
    ).filter(Transaction.account_id == account_id).first()

    return AccountSummaryResponse(
        account_id=account.id,
        nickname=account.nickname,
        account_type=account.account_type,
        current_balance=account.current_balance,
        total_transactions=stats.total or 0,
        total_debits=round(stats.total_debits or 0.0, 2),
        total_credits=round(stats.total_credits or 0.0, 2),
    )


# ── Helper ────────────────────────────────────────────────────────

def _get_user_account(
    account_id: int, user_id: int, db: Session
) -> Account:
    """Fetch account and verify ownership. Raises 404 if not found."""
    account = db.query(Account).filter(
        Account.id == account_id,
        Account.user_id == user_id,
    ).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found.",
        )
    return account
