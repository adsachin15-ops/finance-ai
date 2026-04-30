"""
scripts/profile_upload.py
─────────────────────────────────────────────────────────────
Profile the CSV upload pipeline at scale.

Measures time spent in each pipeline stage:
  1. CSV parsing (encoding detection, column mapping)
  2. Batch categorization (rule engine)
  3. Hash computation (dedup fingerprints)
  4. Bulk DB insert (with duplicate handling)

Usage:
  cd ~/workspace/projects/finance-ai
  source venv/bin/activate
  python scripts/profile_upload.py [--rows N]
"""

from __future__ import annotations

import argparse
import csv
import cProfile
import io
import pstats
import random
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.database import init_db, db_session
from backend.core.security import compute_transaction_hash, sanitize_csv_cell
from backend.services.file_parser.csv_parser import CSVParser
from backend.ai.categorizer import get_categorizer
from backend.models.account import Account
from backend.models.transaction import Transaction
from backend.models.user import User
from sqlalchemy.exc import IntegrityError
import backend.models  # noqa


# ── CSV Generation ────────────────────────────────────────────────

DESCRIPTIONS = [
    "SWIGGY ORDER", "ZOMATO DELIVERY", "BIGBASKET ORDER",
    "UBER TRIP MUMBAI", "BPCL PETROL PUMP", "AMAZON PURCHASE",
    "FLIPKART ORDER", "BESCOM ELECTRICITY", "AIRTEL BROADBAND",
    "HOUSE RENT PAYMENT", "APOLLO PHARMACY", "NETFLIX SUBSCRIPTION",
    "HOME LOAN EMI", "SALARY CREDIT", "FREELANCE PAYMENT",
    "UPI TRANSFER", "ATM WITHDRAWAL", "IRCTC TICKET BOOKING",
    "INDIGO AIRLINES", "OYO HOTEL",
]


def generate_csv(rows: int) -> Path:
    """Generate a synthetic bank statement CSV."""
    headers = [
        "Date", "Narration", "Value Date",
        "Debit Amount", "Credit Amount", "Balance"
    ]

    today = date.today()
    balance = 100000.0

    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False,
        mode="w", encoding="utf-8", newline=""
    )
    writer = csv.writer(tmp)
    writer.writerow(headers)

    for i in range(rows):
        tx_date = today - timedelta(days=random.randint(0, 365))
        desc = f"{random.choice(DESCRIPTIONS)} {random.randint(10000, 99999)}"
        is_credit = random.random() < 0.2  # 20% credits
        amount = round(random.uniform(50, 5000), 2)

        if is_credit:
            balance += amount
            writer.writerow([
                tx_date.strftime("%d/%m/%Y"),
                desc,
                tx_date.strftime("%d/%m/%Y"),
                "",
                f"{amount:.2f}",
                f"{balance:.2f}",
            ])
        else:
            balance = max(0, balance - amount)
            writer.writerow([
                tx_date.strftime("%d/%m/%Y"),
                desc,
                tx_date.strftime("%d/%m/%Y"),
                f"{amount:.2f}",
                "",
                f"{balance:.2f}",
            ])

    tmp.close()
    return Path(tmp.name)


# ── Stage Timers ──────────────────────────────────────────────────

class StageTimer:
    def __init__(self, name: str):
        self.name = name
        self._start = None
        self.elapsed_ms = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000

    def __str__(self):
        grade = (
            "🟢" if self.elapsed_ms < 100 else
            "🟡" if self.elapsed_ms < 500 else
            "🟠" if self.elapsed_ms < 2000 else
            "🔴"
        )
        return f"  {grade} {self.name:<35} {self.elapsed_ms:8.1f}ms"


# ── Profile Pipeline ──────────────────────────────────────────────

def profile_pipeline(rows: int, account_id: int) -> None:
    print(f"\n  Pipeline stages for {rows:,} rows:")
    print("  " + "─" * 55)

    # Stage 1: CSV generation (not part of real pipeline — just setup)
    csv_path = generate_csv(rows)

    # Stage 2: CSV parsing
    with StageTimer("1. CSV parsing") as t1:
        parser = CSVParser()
        parsed_rows = parser.parse(csv_path)
    print(t1)
    print(f"     Rows parsed: {len(parsed_rows):,}")

    # Stage 3: Batch categorization
    categorizer = get_categorizer()
    descriptions = [r.get("description", "") or "" for r in parsed_rows]

    with StageTimer("2. Batch categorization") as t2:
        categories = categorizer.batch_categorize(descriptions)
    print(t2)
    print(f"     Rows categorized: {len(categories):,}")

    # Stage 4: Hash computation
    with StageTimer("3. Hash computation") as t3:
        hashes = []
        for row in parsed_rows:
            h = compute_transaction_hash(
                account_id=account_id,
                date=str(row["date"]),
                amount=float(row["amount"]),
                description=row.get("description", ""),
                transaction_type=row["type"],
            )
            hashes.append(h)
    print(t3)

    # Stage 5: Build Transaction objects
    with StageTimer("4. Build ORM objects") as t4:
        txn_objects = []
        for row, cat, tx_hash in zip(parsed_rows, categories, hashes):
            txn_objects.append(Transaction(
                account_id=account_id,
                date=row["date"],
                amount=abs(float(row["amount"])),
                type=row["type"],
                category=cat.category,
                subcategory=cat.subcategory,
                merchant=cat.merchant,
                description=row.get("description", ""),
                raw_description=row.get("raw_description", ""),
                source="csv",
                hash=tx_hash,
            ))
    print(t4)

    # Stage 6: Bulk DB insert
    inserted = duplicate = failed = 0

    with StageTimer("5. Bulk DB insert") as t5:
        with db_session() as db:
            for tx in txn_objects:
                try:
                    db.add(tx)
                    db.flush()
                    inserted += 1
                except IntegrityError:
                    db.rollback()
                    duplicate += 1
                except Exception:
                    db.rollback()
                    failed += 1
            db.commit()
    print(t5)
    print(f"     Inserted: {inserted:,}  Duplicates: {duplicate:,}  Failed: {failed:,}")

    # Stage 7: Cleanup
    csv_path.unlink(missing_ok=True)

    # Total
    total_ms = t1.elapsed_ms + t2.elapsed_ms + t3.elapsed_ms + t4.elapsed_ms + t5.elapsed_ms
    print("  " + "─" * 55)
    print(f"  Total pipeline:                     {total_ms:8.1f}ms")
    print(f"  Throughput:                         {rows / (total_ms/1000):8.0f} tx/sec")
    print()

    # Bottleneck analysis
    stages = [
        ("CSV parsing",        t1.elapsed_ms),
        ("Categorization",     t2.elapsed_ms),
        ("Hash computation",   t3.elapsed_ms),
        ("Build ORM objects",  t4.elapsed_ms),
        ("DB insert",          t5.elapsed_ms),
    ]
    bottleneck = max(stages, key=lambda x: x[1])
    print(f"  Bottleneck: {bottleneck[0]} ({bottleneck[1]:.1f}ms = {bottleneck[1]/total_ms*100:.0f}% of total)")


# ── cProfile Run ──────────────────────────────────────────────────

def run_cprofile(rows: int, account_id: int) -> None:
    """Run cProfile on the categorizer — most CPU-intensive stage."""
    print("\n  ── cProfile: Batch Categorizer ──\n")

    csv_path = generate_csv(rows)
    parser = CSVParser()
    parsed_rows = parser.parse(csv_path)
    descriptions = [r.get("description", "") or "" for r in parsed_rows]
    csv_path.unlink(missing_ok=True)

    categorizer = get_categorizer()

    profiler = cProfile.Profile()
    profiler.enable()
    categorizer.batch_categorize(descriptions)
    profiler.disable()

    stream = io.StringIO()
    ps = pstats.Stats(profiler, stream=stream)
    ps.sort_stats("cumulative")
    ps.print_stats(15)

    # Print only the stats table
    output = stream.getvalue()
    for line in output.splitlines()[4:25]:
        print(f"  {line}")


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Profile upload pipeline")
    parser.add_argument("--rows", type=int, default=1000,
                        help="Rows to profile (default: 1000)")
    parser.add_argument("--cprofile", action="store_true",
                        help="Run cProfile on categorizer")
    args = parser.parse_args()

    print("\n  ⬡  Finance-AI — Upload Pipeline Profiler")
    print("  ──────────────────────────────────────────")

    init_db()

    with db_session() as db:
        user = db.query(User).filter(
            User.phone_number == "+919800000001"
        ).first()

        if not user:
            print("\n  ✗ No perf test users found.")
            print("    Run: python scripts/generate_test_data.py first.\n")
            sys.exit(1)

        account = db.query(Account).filter(
            Account.user_id == user.id
        ).first()

        if not account:
            print("\n  ✗ No accounts found for perf user.\n")
            sys.exit(1)

        account_id = account.id

    print(f"  Rows: {args.rows:,}")
    print(f"  Account ID: {account_id}")

    profile_pipeline(args.rows, account_id)

    if args.cprofile:
        run_cprofile(args.rows, account_id)

    print()


if __name__ == "__main__":
    main()
