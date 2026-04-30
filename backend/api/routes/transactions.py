"""
backend/api/routes/transactions.py
─────────────────────────────────────────────────────────────
Transaction query and management endpoints.

GET    /transactions/         → paginated list with filters
GET    /transactions/search   → full-text search in descriptions
GET    /transactions/{id}     → single transaction
PUT    /transactions/{id}     → update category/notes
DELETE /transactions/{id}     → delete single transaction

Design:
  - All queries join through Account to enforce user ownership.
  - Pagination prevents memory issues at 50,000+ rows.
  - User corrections to category become ML training data in Phase 2.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_user_or_guest as get_current_user
from backend.core.database import get_db
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.user import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────

class TransactionResponse(BaseModel):
    id: int
    account_id: int
    date: date
    amount: float
    type: str
    category: Optional[str]
    subcategory: Optional[str]
    merchant: Optional[str]
    description: Optional[str]
    source: str
    notes: Optional[str]

    model_config = {"from_attributes": True}


class TransactionUpdate(BaseModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None


class PaginatedTransactions(BaseModel):
    items: List[TransactionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/", response_model=PaginatedTransactions)
async def list_transactions(
    account_id: Optional[int] = None,
    category: Optional[str] = None,
    type: Optional[Literal["debit", "credit"]] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaginatedTransactions:
    """
    Paginated transaction list with optional filters.
    All filters are indexed for performance at 50,000+ rows.
    """
    query = (
        db.query(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
    )

    if account_id:
        query = query.filter(Transaction.account_id == account_id)
    if category:
        query = query.filter(Transaction.category == category)
    if type:
        query = query.filter(Transaction.type == type)
    if date_from:
        query = query.filter(Transaction.date >= date_from)
    if date_to:
        query = query.filter(Transaction.date <= date_to)

    total = query.count()
    items = (
        query
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PaginatedTransactions(
        items=[TransactionResponse.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, -(-total // page_size)),
    )


@router.get("/search", response_model=List[TransactionResponse])
async def search_transactions(
    q: str = Query(..., min_length=2),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[TransactionResponse]:
    """Full-text search across description and merchant fields."""
    results = (
        db.query(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .filter(
            Account.user_id == current_user.id,
            or_(
                Transaction.description.ilike(f"%{q}%"),
                Transaction.merchant.ilike(f"%{q}%"),
                Transaction.raw_description.ilike(f"%{q}%"),
            ),
        )
        .order_by(Transaction.date.desc())
        .limit(limit)
        .all()
    )
    return [TransactionResponse.model_validate(t) for t in results]


@router.get("/{tx_id}", response_model=TransactionResponse)
async def get_transaction(
    tx_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransactionResponse:
    """Get a single transaction by ID."""
    tx = _get_user_transaction(tx_id, current_user.id, db)
    return TransactionResponse.model_validate(tx)


@router.put("/{tx_id}", response_model=TransactionResponse)
async def update_transaction(
    tx_id: int,
    body: TransactionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransactionResponse:
    """
    Manually correct category, notes, or description.
    These corrections become training labels for Phase 2 ML model.
    """
    tx = _get_user_transaction(tx_id, current_user.id, db)
    updates = body.model_dump(exclude_none=True)

    # Sanitize string fields before DB write
    _MAX = {"category": 50, "subcategory": 50, "notes": 500, "description": 500}
    for field, value in updates.items():
        if isinstance(value, str):
            value = value.strip().replace("\x00", "")[:_MAX.get(field, 500)]
        setattr(tx, field, value)

    db.commit()
    db.refresh(tx)
    return TransactionResponse.model_validate(tx)


@router.delete("/{tx_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_transaction(
    tx_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a single transaction."""
    tx = _get_user_transaction(tx_id, current_user.id, db)
    db.delete(tx)
    db.commit()


# ── Helper ────────────────────────────────────────────────────────

def _get_user_transaction(
    tx_id: int, user_id: int, db: Session
) -> Transaction:
    """Fetch transaction and verify ownership. Raises 404 if not found."""
    tx = (
        db.query(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .filter(
            Transaction.id == tx_id,
            Account.user_id == user_id,
        )
        .first()
    )
    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found.",
        )
    return tx
