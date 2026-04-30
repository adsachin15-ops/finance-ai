"""
backend/api/routes/dashboard.py
─────────────────────────────────────────────────────────────
Dashboard aggregation endpoints.

GET /dashboard/summary  → KPIs: income, expenses, savings, health score
GET /dashboard/trend    → spending trend over time (line/bar chart data)
GET /dashboard/heatmap  → daily spending intensity (calendar view data)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_user_or_guest as get_current_user
from backend.core.database import get_db
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.user import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────

class PeriodSummary(BaseModel):
    period_start: date
    period_end: date
    total_income: float
    total_expenses: float
    net_savings: float
    savings_rate: float
    transaction_count: int
    financial_health_score: int


class CategoryBreakdown(BaseModel):
    category: str
    total_amount: float
    transaction_count: int
    percentage: float


class AccountBalance(BaseModel):
    account_id: int
    nickname: str
    account_type: str
    current_balance: float
    credit_utilization: Optional[float]
    currency: str


class DashboardSummaryResponse(BaseModel):
    summary: PeriodSummary
    top_categories: List[CategoryBreakdown]
    account_balances: List[AccountBalance]


class TrendPoint(BaseModel):
    period_label: str
    income: float
    expenses: float
    net: float


class HeatmapEntry(BaseModel):
    date: date
    amount: float
    transaction_count: int


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    period: str = Query(
        "monthly",
        pattern="^(daily|weekly|monthly|yearly)$",
    ),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    account_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DashboardSummaryResponse:
    """
    Main dashboard summary.
    Period presets when date_from/date_to not provided:
      daily   → today
      weekly  → last 7 days
      monthly → current calendar month
      yearly  → current calendar year
    """
    start, end = _resolve_period(period, date_from, date_to)

    base_q = (
        db.query(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .filter(
            Account.user_id == current_user.id,
            Transaction.date >= start,
            Transaction.date <= end,
        )
    )
    if account_id:
        base_q = base_q.filter(Transaction.account_id == account_id)

    totals = base_q.with_entities(
        func.sum(
            case((Transaction.type == "credit", Transaction.amount), else_=0)
        ).label("income"),
        func.sum(
            case((Transaction.type == "debit", Transaction.amount), else_=0)
        ).label("expenses"),
        func.count(Transaction.id).label("count"),
    ).first()

    income = round(totals.income or 0.0, 2)
    expenses = round(totals.expenses or 0.0, 2)
    net = round(income - expenses, 2)
    savings_rate = round(
        (net / income * 100) if income > 0 else 0.0, 1
    )
    tx_count = totals.count or 0

    # Top spending categories
    cat_data = (
        base_q
        .filter(Transaction.type == "debit")
        .with_entities(
            Transaction.category,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("cnt"),
        )
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(8)
        .all()
    )

    top_categories = [
        CategoryBreakdown(
            category=row.category or "Other",
            total_amount=round(row.total, 2),
            transaction_count=row.cnt,
            percentage=round(
                (row.total / expenses * 100) if expenses > 0 else 0.0, 1
            ),
        )
        for row in cat_data
    ]

    # Account balances
    accounts = db.query(Account).filter(
        Account.user_id == current_user.id,
        Account.is_active == True,
    ).all()

    account_balances = [
        AccountBalance(
            account_id=a.id,
            nickname=a.nickname,
            account_type=a.account_type,
            current_balance=a.current_balance,
            credit_utilization=a.credit_utilization,
            currency=a.currency,
        )
        for a in accounts
    ]

    return DashboardSummaryResponse(
        summary=PeriodSummary(
            period_start=start,
            period_end=end,
            total_income=income,
            total_expenses=expenses,
            net_savings=net,
            savings_rate=savings_rate,
            transaction_count=tx_count,
            financial_health_score=_compute_health_score(
                income, expenses, savings_rate
            ),
        ),
        top_categories=top_categories,
        account_balances=account_balances,
    )


@router.get("/trend", response_model=List[TrendPoint])
async def get_spending_trend(
    granularity: str = Query(
        "monthly",
        pattern="^(daily|weekly|monthly)$",
    ),
    months: int = Query(6, ge=1, le=24),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[TrendPoint]:
    """Spending trend over time for chart rendering."""
    import pandas as pd

    end = date.today()
    start = date(
        end.year if end.month > months else end.year - 1,
        ((end.month - months - 1) % 12) + 1,
        1,
    )

    rows = (
        db.query(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .filter(
            Account.user_id == current_user.id,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .with_entities(
            Transaction.date,
            Transaction.type,
            Transaction.amount,
        )
        .all()
    )

    if not rows:
        return []

    df = pd.DataFrame(rows, columns=["date", "type", "amount"])
    df["date"] = pd.to_datetime(df["date"])

    if granularity == "monthly":
        df["period"] = df["date"].dt.to_period("M").astype(str)
    elif granularity == "weekly":
        df["period"] = df["date"].dt.to_period("W").apply(
            lambda x: str(x.start_time.date())
        )
    else:
        df["period"] = df["date"].dt.strftime("%Y-%m-%d")

    grouped = (
        df.groupby(["period", "type"])["amount"]
        .sum()
        .unstack(fill_value=0)
        .reset_index()
    )

    result = []
    for _, row in grouped.sort_values("period").iterrows():
        income = round(float(row.get("credit", 0)), 2)
        expenses = round(float(row.get("debit", 0)), 2)
        result.append(TrendPoint(
            period_label=row["period"],
            income=income,
            expenses=expenses,
            net=round(income - expenses, 2),
        ))

    return result


@router.get("/heatmap", response_model=List[HeatmapEntry])
async def get_spending_heatmap(
    days: int = Query(90, ge=7, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[HeatmapEntry]:
    """Daily spending intensity for calendar heatmap."""
    end = date.today()
    start = end - timedelta(days=days)

    rows = (
        db.query(
            Transaction.date,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .filter(
            Account.user_id == current_user.id,
            Transaction.type == "debit",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .group_by(Transaction.date)
        .order_by(Transaction.date)
        .all()
    )

    return [
        HeatmapEntry(
            date=row.date,
            amount=round(row.total, 2),
            transaction_count=row.count,
        )
        for row in rows
    ]


# ── Helpers ───────────────────────────────────────────────────────

def _resolve_period(
    period: str,
    date_from: Optional[date],
    date_to: Optional[date],
) -> tuple[date, date]:
    if date_from and date_to:
        return date_from, date_to
    today = date.today()
    if period == "daily":
        return today, today
    elif period == "weekly":
        return today - timedelta(days=6), today
    elif period == "monthly":
        return today.replace(day=1), today
    elif period == "yearly":
        return today.replace(month=1, day=1), today
    return today.replace(day=1), today


def _compute_health_score(
    income: float,
    expenses: float,
    savings_rate: float,
) -> int:
    """
    Financial Health Score 0-100.
      savings >= 30% → 40 pts
      savings 20-29% → 30 pts
      savings 10-19% → 20 pts
      savings > 0%   → 10 pts
      expenses < income → 30 pts
      has income data   → 20 pts
      income > 0        → 10 pts
    """
    score = 0
    if income > 0:
        score += 10
        if expenses < income:
            score += 30
        if savings_rate >= 30:
            score += 40
        elif savings_rate >= 20:
            score += 30
        elif savings_rate >= 10:
            score += 20
        elif savings_rate > 0:
            score += 10
    if income > 0 and expenses > 0:
        score += 20
    return min(100, max(0, score))
