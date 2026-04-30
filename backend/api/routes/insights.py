"""
backend/api/routes/insights.py
─────────────────────────────────────────────────────────────
AI Insights endpoints.

GET /insights/          → list all insights for current user
PUT /insights/{id}/read → mark a single insight as read
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_user_or_guest as get_current_user
from backend.core.database import get_db
from backend.models.insight import Insight
from backend.models.user import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────

class InsightResponse(BaseModel):
    id: int
    insight_type: str
    title: str
    body: str
    severity: str
    period_start: Optional[date]
    period_end: Optional[date]
    is_read: bool

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/", response_model=List[InsightResponse])
async def list_insights(
    unread_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[InsightResponse]:
    """Return AI-generated insights for the authenticated user."""
    query = db.query(Insight).filter(
        Insight.user_id == current_user.id
    )
    if unread_only:
        query = query.filter(Insight.is_read == False)
    insights = (
        query
        .order_by(Insight.generated_at.desc())
        .limit(50)
        .all()
    )
    return [InsightResponse.model_validate(i) for i in insights]


@router.put("/{insight_id}/read", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def mark_insight_read(
    insight_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Mark a single insight as read."""
    insight = db.query(Insight).filter(
        Insight.id == insight_id,
        Insight.user_id == current_user.id,
    ).first()
    if insight:
        insight.is_read = True
        db.commit()
