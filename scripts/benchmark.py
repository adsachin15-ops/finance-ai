"""
scripts/benchmark.py
─────────────────────────────────────────────────────────────
Query benchmark suite for Finance-AI.

Measures execution time of all critical query patterns
at scale (50K+ transactions).

Benchmarks:
  1. Dashboard summary (income/expense aggregation)
  2. Category breakdown query
  3. Transaction pagination (page 1, page 500)
  4. Date range filter (1 month, 1 year)
  5. Full-text search
  6. Heatmap aggregation
  7. Upload duplicate detection (file hash lookup)
  8. Transaction hash lookup (dedup check)
  9. Account balance summary
  10. Insight engine data fetch

Usage:
  cd ~/workspace/projects/finance-ai
  source venv/bin/activate
  python scripts/benchmark.py [--runs N]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.database import init_db, db_session
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.upload_log import UploadLog
from backend.models.user import User
from sqlalchemy import func, case, text, or_
import backend.models  # noqa


# ── Benchmark Runner ──────────────────────────────────────────────

class BenchmarkResult:
    def __init__(self, name: str, times: list[float]):
        self.name = name
        self.times = times
        self.mean = statistics.mean(times) * 1000    # ms
        self.median = statistics.median(times) * 1000
        self.min = min(times) * 1000
        self.max = max(times) * 1000
        self.p95 = sorted(times)[int(len(times) * 0.95)] * 1000

    def grade(self) -> str:
        if self.median < 5:    return "🟢 FAST"
        if self.median < 50:   return "🟡 OK"
        if self.median < 200:  return "🟠 SLOW"
        return "🔴 CRITICAL"

    def __str__(self):
        return (
            f"  {self.grade():<14} {self.name:<45} "
            f"median={self.median:6.1f}ms  "
            f"p95={self.p95:6.1f}ms  "
            f"min={self.min:5.1f}ms  "
            f"max={self.max:5.1f}ms"
        )


def benchmark(name: str, fn, runs: int = 10) -> BenchmarkResult:
    """Run a function N times and collect timing data."""
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return BenchmarkResult(name, times)


# ── Benchmark Queries ─────────────────────────────────────────────

def run_benchmarks(runs: int = 10) -> list[BenchmarkResult]:
    results = []

    init_db()

    with db_session() as db:
        # Get a test user
        user = db.query(User).filter(
            User.phone_number == "+919800000001"
        ).first()

        if not user:
            print("\n  ✗ No perf test users found.")
            print("    Run: python scripts/generate_test_data.py first.\n")
            sys.exit(1)

        user_id = user.id
        today = date.today()
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)
        week_start = today - timedelta(days=6)

        # Get first account ID for account-specific queries
        first_account = db.query(Account).filter(
            Account.user_id == user_id
        ).first()
        account_id = first_account.id if first_account else None

        print(f"\n  User ID: {user_id}")
        print(f"  Account ID: {account_id}")

        # Count total transactions for this user
        total_tx = (
            db.query(func.count(Transaction.id))
            .join(Account, Transaction.account_id == Account.id)
            .filter(Account.user_id == user_id)
            .scalar()
        )
        print(f"  Total transactions: {total_tx:,}")
        print(f"  Runs per benchmark: {runs}")
        print()

        # ── B1: Dashboard monthly summary ────────────────────────
        def b1_dashboard_monthly():
            db.query(
                func.sum(case(
                    (Transaction.type == "credit", Transaction.amount),
                    else_=0
                )).label("income"),
                func.sum(case(
                    (Transaction.type == "debit", Transaction.amount),
                    else_=0
                )).label("expenses"),
                func.count(Transaction.id).label("count"),
            ).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                Transaction.date >= month_start,
                Transaction.date <= today,
            ).first()

        results.append(benchmark("Dashboard: monthly summary", b1_dashboard_monthly, runs))

        # ── B2: Dashboard yearly summary ─────────────────────────
        def b2_dashboard_yearly():
            db.query(
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count"),
            ).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                Transaction.date >= year_start,
                Transaction.date <= today,
            ).first()

        results.append(benchmark("Dashboard: yearly summary", b2_dashboard_yearly, runs))

        # ── B3: Category breakdown ────────────────────────────────
        def b3_category_breakdown():
            db.query(
                Transaction.category,
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("cnt"),
            ).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                Transaction.type == "debit",
                Transaction.date >= month_start,
            ).group_by(Transaction.category).all()

        results.append(benchmark("Dashboard: category breakdown", b3_category_breakdown, runs))

        # ── B4: Transaction list page 1 ───────────────────────────
        def b4_tx_page1():
            db.query(Transaction).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id
            ).order_by(
                Transaction.date.desc(),
                Transaction.id.desc()
            ).offset(0).limit(50).all()

        results.append(benchmark("Transactions: list page 1 (50 rows)", b4_tx_page1, runs))

        # ── B5: Transaction list deep page ────────────────────────
        def b5_tx_deep_page():
            db.query(Transaction).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id
            ).order_by(
                Transaction.date.desc(),
                Transaction.id.desc()
            ).offset(10000).limit(50).all()

        results.append(benchmark("Transactions: list page 200 (deep offset)", b5_tx_deep_page, runs))

        # ── B6: Date range filter (1 month) ───────────────────────
        def b6_date_range_month():
            db.query(Transaction).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                Transaction.date >= month_start,
                Transaction.date <= today,
            ).all()

        results.append(benchmark("Transactions: filter 1 month", b6_date_range_month, runs))

        # ── B7: Full-text search ──────────────────────────────────
        def b7_search():
            db.query(Transaction).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                or_(
                    Transaction.description.ilike("%swiggy%"),
                    Transaction.merchant.ilike("%swiggy%"),
                )
            ).limit(50).all()

        results.append(benchmark("Transactions: full-text search", b7_search, runs))

        # ── B8: Heatmap aggregation ───────────────────────────────
        def b8_heatmap():
            db.query(
                Transaction.date,
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count"),
            ).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                Transaction.type == "debit",
                Transaction.date >= today - timedelta(days=90),
            ).group_by(Transaction.date).all()

        results.append(benchmark("Dashboard: 90-day heatmap", b8_heatmap, runs))

        # ── B9: Account summary ───────────────────────────────────
        def b9_account_summary():
            if account_id:
                db.query(
                    func.count(Transaction.id).label("total"),
                    func.sum(case(
                        (Transaction.type == "debit", Transaction.amount),
                        else_=0
                    )).label("debits"),
                    func.sum(case(
                        (Transaction.type == "credit", Transaction.amount),
                        else_=0
                    )).label("credits"),
                ).filter(Transaction.account_id == account_id).first()

        results.append(benchmark("Accounts: summary aggregation", b9_account_summary, runs))

        # ── B10: COUNT all user transactions ──────────────────────
        def b10_count():
            db.query(func.count(Transaction.id)).join(
                Account, Transaction.account_id == Account.id
            ).filter(Account.user_id == user_id).scalar()

        results.append(benchmark("Transactions: total count", b10_count, runs))

        # ── B11: Spending trend (monthly grouping) ────────────────
        def b11_trend():
            rows = db.query(
                Transaction.date,
                Transaction.type,
                Transaction.amount,
            ).join(
                Account, Transaction.account_id == Account.id
            ).filter(
                Account.user_id == user_id,
                Transaction.date >= year_start,
            ).all()
            return rows

        results.append(benchmark("Dashboard: yearly trend data fetch", b11_trend, runs))

        # ── B12: Upload log hash lookup ───────────────────────────
        def b12_hash_lookup():
            db.query(UploadLog).filter(
                UploadLog.user_id == user_id,
                UploadLog.file_hash == "a" * 64,
                UploadLog.status == "completed",
            ).first()

        results.append(benchmark("Upload: file hash duplicate check", b12_hash_lookup, runs))

    return results


# ── EXPLAIN ANALYZE ───────────────────────────────────────────────

def run_explain(user_id: int) -> None:
    """Run EXPLAIN QUERY PLAN on the slowest queries."""
    print("\n  ── EXPLAIN QUERY PLAN (critical queries) ──\n")

    with db_session() as db:
        today = date.today()
        month_start = today.replace(day=1)

        queries = [
            (
                "Dashboard monthly summary",
                text("""
                    EXPLAIN QUERY PLAN
                    SELECT
                        SUM(CASE WHEN t.type='credit' THEN t.amount ELSE 0 END),
                        SUM(CASE WHEN t.type='debit' THEN t.amount ELSE 0 END),
                        COUNT(t.id)
                    FROM transactions t
                    JOIN accounts a ON t.account_id = a.id
                    WHERE a.user_id = :uid
                    AND t.date >= :start
                    AND t.date <= :end
                """),
                {"uid": user_id, "start": month_start, "end": today},
            ),
            (
                "Transaction list with ORDER BY",
                text("""
                    EXPLAIN QUERY PLAN
                    SELECT t.id FROM transactions t
                    JOIN accounts a ON t.account_id = a.id
                    WHERE a.user_id = :uid
                    ORDER BY t.date DESC, t.id DESC
                    LIMIT 50 OFFSET 0
                """),
                {"uid": user_id},
            ),
            (
                "Category breakdown",
                text("""
                    EXPLAIN QUERY PLAN
                    SELECT t.category, SUM(t.amount), COUNT(t.id)
                    FROM transactions t
                    JOIN accounts a ON t.account_id = a.id
                    WHERE a.user_id = :uid
                    AND t.type = 'debit'
                    AND t.date >= :start
                    GROUP BY t.category
                """),
                {"uid": user_id, "start": month_start},
            ),
        ]

        for name, q, params in queries:
            print(f"  Query: {name}")
            rows = db.execute(q, params).fetchall()
            for row in rows:
                print(f"    {row}")
            print()


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Finance-AI benchmark suite")
    parser.add_argument("--runs", type=int, default=10,
                        help="Benchmark runs per query (default: 10)")
    parser.add_argument("--explain", action="store_true",
                        help="Show EXPLAIN QUERY PLAN for critical queries")
    args = parser.parse_args()

    print("\n  ⬡  Finance-AI — Query Benchmark Suite")
    print("  ──────────────────────────────────────")

    results = run_benchmarks(runs=args.runs)

    print("  Results:")
    print("  " + "─" * 90)
    for r in results:
        print(r)
    print("  " + "─" * 90)

    # Summary
    critical = [r for r in results if r.median >= 200]
    slow = [r for r in results if 50 <= r.median < 200]
    ok = [r for r in results if r.median < 50]

    print(f"\n  Summary:")
    print(f"    🟢 Fast  (<50ms):     {len(ok)}")
    print(f"    🟠 Slow  (50-200ms):  {len(slow)}")
    print(f"    🔴 Critical (>200ms): {len(critical)}")

    if critical:
        print(f"\n  Critical queries requiring index fixes:")
        for r in critical:
            print(f"    - {r.name}")

    if args.explain:
        init_db()
        with db_session() as db:
            user = db.query(User).filter(
                User.phone_number == "+919800000001"
            ).first()
            if user:
                run_explain(user.id)

    print()


if __name__ == "__main__":
    main()
