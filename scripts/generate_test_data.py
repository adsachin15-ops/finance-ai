"""
scripts/generate_test_data.py
─────────────────────────────────────────────────────────────
Generate realistic test data for performance profiling.

Generates:
  - 3 users
  - 5 accounts per user
  - 50,000 transactions spread across accounts and dates

Usage:
  cd ~/workspace/projects/finance-ai
  source venv/bin/activate
  python scripts/generate_test_data.py [--transactions N]

WARNING: This modifies the real database.
         Run only on a dev/test instance.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.database import init_db, db_session
from backend.core.security import hash_pin, compute_transaction_hash
from backend.models.user import User
from backend.models.account import Account
from backend.models.transaction import Transaction
import backend.models  # noqa: F401 — ensure all models registered


# ── Config ────────────────────────────────────────────────────────

USERS = [
    {"phone": "+919800000001", "name": "Perf User 1"},
    {"phone": "+919800000002", "name": "Perf User 2"},
    {"phone": "+919800000003", "name": "Perf User 3"},
]

ACCOUNT_TYPES = [
    ("savings", "HDFC Bank"),
    ("credit_card", "ICICI Bank"),
    ("wallet", "Paytm"),
    ("savings", "SBI"),
    ("upi", "PhonePe"),
]

CATEGORIES = [
    ("Food", "Swiggy"),
    ("Food", "Zomato"),
    ("Food", "Groceries"),
    ("Travel", "Uber"),
    ("Travel", "Fuel"),
    ("Shopping", "Amazon"),
    ("Shopping", "Flipkart"),
    ("Bills", "Electricity"),
    ("Bills", "Internet"),
    ("Bills", "Rent"),
    ("Health", "Pharmacy"),
    ("Entertainment", "Streaming"),
    ("Finance", "Loan EMI"),
    ("Income", "Salary"),
    ("Income", "Freelance"),
    ("Transfer", "UPI Transfer"),
    ("Other", None),
]

DESCRIPTIONS = [
    "SWIGGY ORDER", "ZOMATO DELIVERY", "BIGBASKET ORDER",
    "UBER TRIP", "BPCL PETROL", "AMAZON PURCHASE",
    "FLIPKART ORDER", "BESCOM BILL", "AIRTEL BROADBAND",
    "HOUSE RENT", "APOLLO PHARMACY", "NETFLIX SUBSCRIPTION",
    "HOME LOAN EMI", "SALARY CREDIT", "FREELANCE PAYMENT",
    "UPI TRANSFER", "ATM WITHDRAWAL", "IRCTC TICKET",
    "INDIGO AIRLINES", "OYO HOTEL BOOKING",
]


def generate_transactions(
    account_id: int,
    count: int,
    start_date: date,
    end_date: date,
) -> list[Transaction]:
    """Generate random transactions for one account."""
    txns = []
    date_range = (end_date - start_date).days

    for _ in range(count):
        tx_date = start_date + timedelta(days=random.randint(0, date_range))
        cat, subcat = random.choice(CATEGORIES)
        desc = random.choice(DESCRIPTIONS)

        # Income transactions are less frequent
        is_credit = cat == "Income" or (cat == "Transfer" and random.random() < 0.3)
        tx_type = "credit" if is_credit else "debit"

        # Realistic amount ranges
        if cat == "Income":
            amount = round(random.uniform(15000, 80000), 2)
        elif cat == "Bills" and subcat == "Rent":
            amount = round(random.uniform(8000, 25000), 2)
        elif cat == "Finance":
            amount = round(random.uniform(5000, 30000), 2)
        elif cat == "Shopping":
            amount = round(random.uniform(100, 5000), 2)
        elif cat == "Food":
            amount = round(random.uniform(50, 1500), 2)
        else:
            amount = round(random.uniform(50, 3000), 2)

        tx_hash = compute_transaction_hash(
            account_id=account_id,
            date=str(tx_date),
            amount=amount,
            description=f"{desc}_{random.randint(100000, 999999)}",
            transaction_type=tx_type,
        )

        txns.append(Transaction(
            account_id=account_id,
            date=tx_date,
            amount=amount,
            type=tx_type,
            category=cat,
            subcategory=subcat,
            merchant=subcat,
            description=desc,
            raw_description=desc,
            source="csv",
            hash=tx_hash,
        ))

    return txns


def main():
    parser = argparse.ArgumentParser(description="Generate test data")
    parser.add_argument(
        "--transactions", type=int, default=50000,
        help="Total transactions to generate (default: 50000)"
    )
    parser.add_argument(
        "--days", type=int, default=730,
        help="Date range in days (default: 730 = 2 years)"
    )
    args = parser.parse_args()

    print(f"\n  ⬡  Finance-AI — Test Data Generator")
    print(f"  ─────────────────────────────────────")
    print(f"  Transactions: {args.transactions:,}")
    print(f"  Date range:   {args.days} days")
    print(f"  Users:        {len(USERS)}")
    print()

    init_db()

    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)

    total_inserted = 0
    start_time = time.perf_counter()

    with db_session() as db:
        # Check for existing perf users
        existing = db.query(User).filter(
            User.phone_number == USERS[0]["phone"]
        ).first()

        if existing:
            print("  ⚠  Perf users already exist. Skipping user/account creation.")
            print("     Delete them manually to regenerate.\n")
            return

        # Create users and accounts
        user_accounts: list[tuple[User, list[Account]]] = []

        for u_data in USERS:
            user = User(
                phone_number=u_data["phone"],
                pin_hash=hash_pin("1234"),
                display_name=u_data["name"],
            )
            db.add(user)
            db.flush()

            accounts = []
            for acc_type, bank in ACCOUNT_TYPES:
                acc = Account(
                    user_id=user.id,
                    nickname=f"{bank} {acc_type.replace('_', ' ').title()}",
                    bank_name=bank,
                    account_type=acc_type,
                    currency="INR",
                    current_balance=round(random.uniform(5000, 100000), 2),
                    credit_limit=100000.0 if acc_type == "credit_card" else None,
                )
                db.add(acc)
                accounts.append(acc)

            db.flush()
            user_accounts.append((user, accounts))
            print(f"  ✓ Created user: {u_data['name']} ({user.id})")

        # Distribute transactions across users and accounts
        txns_per_account = args.transactions // (len(USERS) * len(ACCOUNT_TYPES))
        batch_size = 500

        print(f"\n  Generating {args.transactions:,} transactions...")
        print(f"  (~{txns_per_account:,} per account)\n")

        for user, accounts in user_accounts:
            for acc in accounts:
                txns = generate_transactions(
                    account_id=acc.id,
                    count=txns_per_account,
                    start_date=start_date,
                    end_date=end_date,
                )

                # Batch insert
                inserted = 0
                for i in range(0, len(txns), batch_size):
                    batch = txns[i:i + batch_size]
                    db.bulk_save_objects(batch)
                    db.flush()
                    inserted += len(batch)

                total_inserted += inserted
                print(
                    f"  ✓ {user.display_name} / {acc.nickname}: "
                    f"{inserted:,} transactions"
                )

        db.commit()

    elapsed = time.perf_counter() - start_time
    print(f"\n  ─────────────────────────────────────")
    print(f"  Total inserted:  {total_inserted:,} transactions")
    print(f"  Time elapsed:    {elapsed:.2f}s")
    print(f"  Throughput:      {total_inserted / elapsed:,.0f} tx/sec")
    print()


if __name__ == "__main__":
    main()
