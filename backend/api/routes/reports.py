"""
backend/api/routes/reports.py
─────────────────────────────────────────────────────────────
Downloadable report generation endpoints.

All reports are streamed directly from memory — no temp files.
No data is written to disk during export.

Endpoints:
  GET /reports/csv            → all transactions as CSV
  GET /reports/summary/csv    → category breakdown as CSV
  GET /reports/excel          → full .xlsx workbook (3 sheets)
  GET /reports/monthly        → per-month CSVs in a .zip archive

Security:
  - All endpoints require valid Bearer token.
  - Users can only export their own data.
  - File names include date range to prevent confusion.

Why no PDF?
  PDF generation requires weasyprint or reportlab which need
  system-level C libraries. Added in Phase 3 as optional feature.
  For now, Excel covers all structured reporting needs.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_user_or_guest as get_current_user
from backend.core.database import get_db
from backend.core.logger import get_logger
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.user import User

log = get_logger(__name__)
router = APIRouter()


# ── Column Definitions ────────────────────────────────────────────

TX_COLUMNS = [
    "id", "date", "amount", "type", "category",
    "subcategory", "merchant", "description", "source", "notes",
    "account_nickname", "account_type", "currency",
]


# ── Shared Query Helper ───────────────────────────────────────────

def _fetch_transactions(
    user_id: int,
    db: Session,
    date_from: Optional[date],
    date_to: Optional[date],
    account_id: Optional[int],
) -> list[dict]:
    """
    Fetch transactions with account info joined.
    Returns list of flat dicts ready for CSV/Excel export.

    Args:
        user_id:    Authenticated user — enforces data ownership.
        db:         SQLAlchemy session.
        date_from:  Start of date range (inclusive).
        date_to:    End of date range (inclusive).
        account_id: Optional account filter.

    Returns:
        List of dicts with TX_COLUMNS keys.
    """
    query = (
        db.query(Transaction, Account)
        .join(Account, Transaction.account_id == Account.id)
        .filter(Account.user_id == user_id)
    )

    if date_from:
        query = query.filter(Transaction.date >= date_from)
    if date_to:
        query = query.filter(Transaction.date <= date_to)
    if account_id:
        query = query.filter(Transaction.account_id == account_id)

    query = query.order_by(Transaction.date.asc(), Transaction.id.asc())

    # Cap at 100,000 rows to prevent OOM on large datasets
    MAX_EXPORT_ROWS = 100_000
    rows = []
    for tx, acc in query.limit(MAX_EXPORT_ROWS).all():
        rows.append({
            "id": tx.id,
            "date": str(tx.date),
            "amount": tx.amount,
            "type": tx.type,
            "category": tx.category or "",
            "subcategory": tx.subcategory or "",
            "merchant": tx.merchant or "",
            "description": tx.description or "",
            "source": tx.source,
            "notes": tx.notes or "",
            "account_nickname": acc.nickname,
            "account_type": acc.account_type,
            "currency": acc.currency,
        })

    return rows


def _resolve_dates(
    period: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
) -> tuple[Optional[date], Optional[date]]:
    """
    Resolve date range from period preset or explicit dates.

    Period presets:
      monthly → current calendar month
      weekly  → last 7 days
      yearly  → current year
    """
    if date_from or date_to:
        return date_from, date_to

    today = date.today()

    if period == "monthly":
        return today.replace(day=1), today
    elif period == "weekly":
        return today - timedelta(days=6), today
    elif period == "yearly":
        return today.replace(month=1, day=1), today

    return None, None


def _filename_suffix(date_from: Optional[date], date_to: Optional[date]) -> str:
    """Build date-range suffix for filenames."""
    if date_from and date_to:
        return f"{date_from}_{date_to}"
    elif date_from:
        return f"from_{date_from}"
    elif date_to:
        return f"to_{date_to}"
    return "all"


# ── CSV Export ────────────────────────────────────────────────────

@router.get("/csv", summary="Export all transactions as CSV")
async def export_csv(
    period: Optional[str] = Query(None, pattern="^(monthly|weekly|yearly)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    account_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Download all transactions as a CSV file.

    CSV columns:
      id, date, amount, type, category, subcategory,
      merchant, description, source, notes,
      account_nickname, account_type, currency

    Usage:
      GET /api/v1/reports/csv
      GET /api/v1/reports/csv?period=monthly
      GET /api/v1/reports/csv?date_from=2024-01-01&date_to=2024-01-31
    """
    d_from, d_to = _resolve_dates(period, date_from, date_to)
    rows = _fetch_transactions(current_user.id, db, d_from, d_to, account_id)

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=TX_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    suffix = _filename_suffix(d_from, d_to)
    filename = f"transactions_{suffix}.csv"

    log.info(
        "report.csv.generated",
        user_id=current_user.id,
        rows=len(rows),
        filename=filename,
    )

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Records": str(len(rows)),
        },
    )


# ── Category Summary CSV ──────────────────────────────────────────

@router.get("/summary/csv", summary="Export category spending summary as CSV")
async def export_summary_csv(
    period: Optional[str] = Query(None, pattern="^(monthly|weekly|yearly)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    account_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Download category-level spending summary as CSV.

    CSV columns:
      category, total_amount, transaction_count,
      percentage, avg_transaction

    Only includes debit (expense) transactions.
    """
    d_from, d_to = _resolve_dates(period, date_from, date_to)
    rows = _fetch_transactions(current_user.id, db, d_from, d_to, account_id)

    # Aggregate by category (debits only)
    cat_data: dict[str, dict] = {}
    total_expense = 0.0

    for row in rows:
        if row["type"] != "debit":
            continue
        cat = row["category"] or "Other"
        if cat not in cat_data:
            cat_data[cat] = {"total": 0.0, "count": 0}
        cat_data[cat]["total"] += row["amount"]
        cat_data[cat]["count"] += 1
        total_expense += row["amount"]

    # Build summary rows sorted by total descending
    summary_rows = []
    for cat, data in sorted(
        cat_data.items(), key=lambda x: x[1]["total"], reverse=True
    ):
        pct = round(data["total"] / total_expense * 100, 2) if total_expense > 0 else 0.0
        avg = round(data["total"] / data["count"], 2) if data["count"] > 0 else 0.0
        summary_rows.append({
            "category": cat,
            "total_amount": round(data["total"], 2),
            "transaction_count": data["count"],
            "percentage": pct,
            "avg_transaction": avg,
        })

    # Add totals row
    summary_rows.append({
        "category": "TOTAL",
        "total_amount": round(total_expense, 2),
        "transaction_count": sum(r["transaction_count"] for r in summary_rows),
        "percentage": 100.0,
        "avg_transaction": round(
            total_expense / len([r for r in rows if r["type"] == "debit"]), 2
        ) if any(r["type"] == "debit" for r in rows) else 0.0,
    })

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["category", "total_amount", "transaction_count",
                    "percentage", "avg_transaction"],
    )
    writer.writeheader()
    writer.writerows(summary_rows)
    output.seek(0)

    suffix = _filename_suffix(d_from, d_to)
    filename = f"category_summary_{suffix}.csv"

    log.info(
        "report.summary_csv.generated",
        user_id=current_user.id,
        categories=len(cat_data),
    )

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ── Excel Export ──────────────────────────────────────────────────

@router.get("/excel", summary="Export full financial report as Excel workbook")
async def export_excel(
    period: Optional[str] = Query(None, pattern="^(monthly|weekly|yearly)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    account_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Download a full Excel workbook (.xlsx) with 3 sheets:

    Sheet 1 — Transactions:
      All transactions with full detail.

    Sheet 2 — Category Summary:
      Debit totals by category, sorted by amount.

    Sheet 3 — Monthly Trend:
      Income, expenses, and savings per month.

    Uses openpyxl via pandas ExcelWriter.
    Streamed from memory — no temp file created.
    """
    import pandas as pd

    d_from, d_to = _resolve_dates(period, date_from, date_to)
    rows = _fetch_transactions(current_user.id, db, d_from, d_to, account_id)

    if not rows:
        # Return empty workbook rather than error
        rows = []

    # ── Sheet 1: Transactions ─────────────────────────────────────
    df_tx = pd.DataFrame(rows, columns=TX_COLUMNS) if rows else pd.DataFrame(columns=TX_COLUMNS)

    # ── Sheet 2: Category Summary ─────────────────────────────────
    if rows:
        df_debits = df_tx[df_tx["type"] == "debit"].copy()
        if not df_debits.empty:
            cat_summary = (
                df_debits.groupby("category")
                .agg(
                    total_amount=("amount", "sum"),
                    transaction_count=("amount", "count"),
                    avg_transaction=("amount", "mean"),
                )
                .reset_index()
                .sort_values("total_amount", ascending=False)
            )
            total_exp = cat_summary["total_amount"].sum()
            cat_summary["percentage"] = (
                cat_summary["total_amount"] / total_exp * 100
            ).round(2)
            cat_summary["total_amount"] = cat_summary["total_amount"].round(2)
            cat_summary["avg_transaction"] = cat_summary["avg_transaction"].round(2)
        else:
            cat_summary = pd.DataFrame(columns=[
                "category", "total_amount", "transaction_count",
                "avg_transaction", "percentage",
            ])
    else:
        cat_summary = pd.DataFrame(columns=[
            "category", "total_amount", "transaction_count",
            "avg_transaction", "percentage",
        ])

    # ── Sheet 3: Monthly Trend ────────────────────────────────────
    if rows:
        df_tx["date"] = pd.to_datetime(df_tx["date"])
        df_tx["month"] = df_tx["date"].dt.to_period("M").astype(str)

        income_by_month = (
            df_tx[df_tx["type"] == "credit"]
            .groupby("month")["amount"].sum()
        )
        expense_by_month = (
            df_tx[df_tx["type"] == "debit"]
            .groupby("month")["amount"].sum()
        )

        all_months = sorted(set(
            list(income_by_month.index) + list(expense_by_month.index)
        ))
        trend_rows = []
        for month in all_months:
            income = round(income_by_month.get(month, 0.0), 2)
            expense = round(expense_by_month.get(month, 0.0), 2)
            trend_rows.append({
                "month": month,
                "income": income,
                "expenses": expense,
                "net_savings": round(income - expense, 2),
                "savings_rate": round(
                    (income - expense) / income * 100, 1
                ) if income > 0 else 0.0,
            })

        df_trend = pd.DataFrame(trend_rows)
    else:
        df_trend = pd.DataFrame(columns=[
            "month", "income", "expenses", "net_savings", "savings_rate"
        ])

    # ── Write to in-memory buffer ─────────────────────────────────
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_tx.to_excel(writer, sheet_name="Transactions", index=False)
        cat_summary.to_excel(writer, sheet_name="Category Summary", index=False)
        df_trend.to_excel(writer, sheet_name="Monthly Trend", index=False)

        # Auto-size columns for readability
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for col in worksheet.columns:
                max_len = max(
                    (len(str(cell.value)) for cell in col if cell.value),
                    default=10,
                )
                worksheet.column_dimensions[
                    col[0].column_letter
                ].width = min(max_len + 2, 50)

    output.seek(0)

    suffix = _filename_suffix(d_from, d_to)
    filename = f"finance_report_{suffix}.xlsx"

    log.info(
        "report.excel.generated",
        user_id=current_user.id,
        rows=len(rows),
        filename=filename,
    )

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Records": str(len(rows)),
        },
    )


# ── Monthly ZIP Export ────────────────────────────────────────────

@router.get("/monthly", summary="Export one CSV per month as a ZIP archive")
async def export_monthly_zip(
    year: int = Query(default=None, ge=2020, le=2100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Download a ZIP archive containing one CSV file per month.

    Each CSV is named: transactions_YYYY-MM.csv
    The ZIP is named: finance_YYYY.zip (or finance_all.zip)

    Useful for:
      - Importing into other tools
      - Monthly bookkeeping
      - Accountant handoff
    """
    # Default to current year if not specified
    target_year = year or date.today().year

    date_from = date(target_year, 1, 1)
    date_to = date(target_year, 12, 31)

    rows = _fetch_transactions(
        current_user.id, db, date_from, date_to, account_id=None
    )

    # Group rows by month
    monthly: dict[str, list[dict]] = {}
    for row in rows:
        month_key = row["date"][:7]  # YYYY-MM
        if month_key not in monthly:
            monthly[month_key] = []
        monthly[month_key].append(row)

    # Build ZIP in memory
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for month_key in sorted(monthly.keys()):
            month_rows = monthly[month_key]

            csv_buffer = io.StringIO()
            writer = csv.DictWriter(csv_buffer, fieldnames=TX_COLUMNS)
            writer.writeheader()
            writer.writerows(month_rows)

            zf.writestr(
                f"transactions_{month_key}.csv",
                csv_buffer.getvalue(),
            )

        # Add a summary CSV
        summary_buf = io.StringIO()
        summary_writer = csv.writer(summary_buf)
        summary_writer.writerow([
            "month", "total_transactions", "total_income",
            "total_expenses", "net_savings",
        ])
        for month_key in sorted(monthly.keys()):
            month_rows = monthly[month_key]
            income = sum(r["amount"] for r in month_rows if r["type"] == "credit")
            expense = sum(r["amount"] for r in month_rows if r["type"] == "debit")
            summary_writer.writerow([
                month_key,
                len(month_rows),
                round(income, 2),
                round(expense, 2),
                round(income - expense, 2),
            ])

        zf.writestr("summary.csv", summary_buf.getvalue())

    zip_buffer.seek(0)
    filename = f"finance_{target_year}.zip"

    log.info(
        "report.monthly_zip.generated",
        user_id=current_user.id,
        year=target_year,
        months=len(monthly),
        total_rows=len(rows),
    )

    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Months": str(len(monthly)),
            "X-Total-Records": str(len(rows)),
        },
    )
