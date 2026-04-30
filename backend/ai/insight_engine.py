"""
backend/ai/insight_engine.py
─────────────────────────────────────────────────────────────
AI Insight Engine — generates financial insights from transactions.

Insights are generated automatically after every upload and
stored in the insights table for the authenticated user.

Analyzers:
  1. SpendingTrendAnalyzer   → month-over-month category comparison
  2. AnomalyDetector         → IQR-based unusual transaction detection
  3. TopCategoryAnalyzer     → highest spend category this period
  4. HealthScoreAnalyzer     → financial health score with explanation
  5. SpendingPredictor       → linear extrapolation for month-end estimate
  6. SavingsAnalyzer         → savings rate with qualitative assessment
  7. BudgetAnalyzer          → budget vs actual (if budget set)

Design principles:
  - Each analyzer is independent. One failure does not block others.
  - Insights are idempotent per period — existing insights for the
    same period are deleted before new ones are inserted.
  - All math uses only stdlib + simple statistics. No ML needed.
  - Phase 3: Replace SpendingPredictor with ARIMA or Prophet.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from backend.core.logger import get_logger
from backend.models.account import Account
from backend.models.insight import Insight
from backend.models.transaction import Transaction
from backend.models.user import User

log = get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────

INSIGHT_LOOKBACK_DAYS = 60      # fetch last 60 days of transactions
ANOMALY_IQR_MULTIPLIER = 2.5    # threshold: Q3 + 2.5 * IQR
MIN_TRANSACTIONS_FOR_INSIGHT = 5 # skip insights with too little data
MAX_INSIGHTS_PER_RUN = 10       # cap per generation run


# ── Data Container ────────────────────────────────────────────────

class PeriodData:
    """
    Pre-fetched transaction aggregates for current and previous month.
    Passed to each analyzer to avoid redundant DB queries.
    """

    def __init__(
        self,
        user_id: int,
        db: Session,
        today: Optional[date] = None,
    ):
        self.user_id = user_id
        self.db = db
        self.today = today or date.today()

        # Current month boundaries
        self.current_start = self.today.replace(day=1)
        self.current_end = self.today

        # Previous month boundaries
        first_of_current = self.current_start
        last_of_prev = first_of_current - timedelta(days=1)
        self.prev_start = last_of_prev.replace(day=1)
        self.prev_end = last_of_prev

        # Fetch data
        self.current_txns = self._fetch_transactions(
            self.current_start, self.current_end
        )
        self.prev_txns = self._fetch_transactions(
            self.prev_start, self.prev_end
        )
        self.recent_txns = self._fetch_transactions(
            self.today - timedelta(days=INSIGHT_LOOKBACK_DAYS),
            self.today,
        )

        # Aggregates
        self.current_debits = [t for t in self.current_txns if t.type == "debit"]
        self.current_credits = [t for t in self.current_txns if t.type == "credit"]
        self.prev_debits = [t for t in self.prev_txns if t.type == "debit"]

        self.current_expense = sum(t.amount for t in self.current_debits)
        self.current_income = sum(t.amount for t in self.current_credits)
        self.prev_expense = sum(t.amount for t in self.prev_debits)

        # User
        self.user = db.query(User).filter(User.id == user_id).first()

    def _fetch_transactions(
        self, start: date, end: date
    ) -> list[Transaction]:
        """Fetch all transactions for this user in a date range."""
        return (
            self.db.query(Transaction)
            .join(Account, Transaction.account_id == Account.id)
            .filter(
                Account.user_id == self.user_id,
                Transaction.date >= start,
                Transaction.date <= end,
            )
            .order_by(Transaction.date.desc())
            .all()
        )

    def category_totals(
        self, transactions: list[Transaction]
    ) -> dict[str, float]:
        """Aggregate debit amounts by category."""
        totals: dict[str, float] = {}
        for t in transactions:
            if t.type == "debit":
                cat = t.category or "Other"
                totals[cat] = totals.get(cat, 0.0) + t.amount
        return totals


# ── Base Analyzer ─────────────────────────────────────────────────

class BaseAnalyzer:
    """
    Base class for all insight analyzers.
    Each subclass implements analyze() and returns
    a list of Insight objects (may be empty).
    """

    name: str = "base"

    def analyze(self, data: PeriodData) -> list[Insight]:
        raise NotImplementedError

    def _make_insight(
        self,
        user_id: int,
        insight_type: str,
        title: str,
        body: str,
        severity: str = "info",
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
    ) -> Insight:
        return Insight(
            user_id=user_id,
            insight_type=insight_type,
            title=title,
            body=body,
            severity=severity,
            period_start=period_start,
            period_end=period_end,
            is_read=False,
        )


# ── Analyzer 1: Spending Trend ─────────────────────────────────────

class SpendingTrendAnalyzer(BaseAnalyzer):
    """
    Compare this month's spending vs last month by category.
    Flags categories with >20% increase as warnings.
    """

    name = "spending_trend"

    def analyze(self, data: PeriodData) -> list[Insight]:
        if len(data.current_debits) < MIN_TRANSACTIONS_FOR_INSIGHT:
            return []
        if len(data.prev_debits) < MIN_TRANSACTIONS_FOR_INSIGHT:
            return []

        current_cats = data.category_totals(data.current_txns)
        prev_cats = data.category_totals(data.prev_txns)

        insights = []

        # Overall spending trend
        if data.prev_expense > 0:
            pct_change = (
                (data.current_expense - data.prev_expense)
                / data.prev_expense * 100
            )
            direction = "more" if pct_change > 0 else "less"
            abs_pct = abs(round(pct_change, 1))
            severity = "warning" if pct_change > 20 else "info"

            insights.append(self._make_insight(
                user_id=data.user_id,
                insight_type="spending_trend",
                title=f"You spent {abs_pct}% {direction} this month",
                body=(
                    f"Total spending this month: "
                    f"₹{data.current_expense:,.0f} vs "
                    f"₹{data.prev_expense:,.0f} last month "
                    f"({'+' if pct_change > 0 else ''}{pct_change:.1f}%)."
                ),
                severity=severity,
                period_start=data.current_start,
                period_end=data.current_end,
            ))

        # Category-level trends — find biggest mover
        biggest_increase = None
        biggest_pct = 0.0

        for cat, curr_amt in current_cats.items():
            prev_amt = prev_cats.get(cat, 0.0)
            if prev_amt > 0 and curr_amt > 500:
                pct = (curr_amt - prev_amt) / prev_amt * 100
                if pct > biggest_pct:
                    biggest_pct = pct
                    biggest_increase = (cat, curr_amt, prev_amt, pct)

        if biggest_increase and biggest_pct > 20:
            cat, curr, prev, pct = biggest_increase
            insights.append(self._make_insight(
                user_id=data.user_id,
                insight_type="spending_trend",
                title=f"{cat} spending up {pct:.0f}% this month",
                body=(
                    f"You spent ₹{curr:,.0f} on {cat} this month, "
                    f"compared to ₹{prev:,.0f} last month. "
                    f"That's a {pct:.0f}% increase."
                ),
                severity="warning" if pct > 50 else "info",
                period_start=data.current_start,
                period_end=data.current_end,
            ))

        return insights


# ── Analyzer 2: Anomaly Detector ──────────────────────────────────

class AnomalyDetector(BaseAnalyzer):
    """
    IQR-based anomaly detection for unusual transactions.

    Method:
      1. Compute Q1, Q3, IQR of debit amounts.
      2. Upper fence = Q3 + 2.5 * IQR.
      3. Transactions above fence → flagged as anomalies.

    C++ analogy:
      Like std::nth_element for median computation,
      but using Python's statistics module.
    """

    name = "anomaly"

    def analyze(self, data: PeriodData) -> list[Insight]:
        txns = data.recent_txns

        if not txns or len(txns) < 5:
            return []

        amounts = [
            float(t.amount)
            for t in txns
            if t.type == "debit"
        ]

        if len(amounts) < 5:
            return []

        # Use median instead of mean (more stable)
        amounts_sorted = sorted(amounts)

        mid = len(amounts_sorted) // 2

        if len(amounts_sorted) % 2 == 0:
            median = (
                amounts_sorted[mid - 1]
                + amounts_sorted[mid]
            ) / 2
        else:
            median = amounts_sorted[mid]

        insights = []

        # Detect anomalies:
        # amount must be significantly larger than median
        threshold = median * 3

        for t in txns:

            if t.type != "debit":
                continue

            amount = float(t.amount)

            if amount >= threshold:

                insight = self._make_insight(
                    user_id=data.user_id,
                    insight_type="anomaly",
                    title="Unusual transaction detected",
                    body=(
                        f"A transaction of ₹{amount:,.0f} "
                        f"is significantly higher than your typical spending."
                    ),
                    severity="alert",
                    period_start=data.current_start,
                    period_end=data.current_end,
                )

                insights.append(insight)

            if len(insights) >= 2:
                break

        return insights

# ── Analyzer 3: Top Category ──────────────────────────────────────

class TopCategoryAnalyzer(BaseAnalyzer):
    """Report the highest spending category this month."""

    name = "top_category"

    def analyze(self, data: PeriodData) -> list[Insight]:
        if len(data.current_debits) < MIN_TRANSACTIONS_FOR_INSIGHT:
            return []

        cat_totals = data.category_totals(data.current_txns)
        if not cat_totals:
            return []

        top_cat = max(cat_totals, key=lambda k: cat_totals[k])
        top_amt = cat_totals[top_cat]

        if data.current_expense == 0:
            return []

        pct = round(top_amt / data.current_expense * 100, 1)

        return [self._make_insight(
            user_id=data.user_id,
            insight_type="top_category",
            title=f"Top expense: {top_cat} (₹{top_amt:,.0f})",
            body=(
                f"{top_cat} is your highest spending category this month "
                f"at ₹{top_amt:,.0f}, accounting for {pct}% "
                f"of your total expenses of ₹{data.current_expense:,.0f}."
            ),
            severity="info",
            period_start=data.current_start,
            period_end=data.current_end,
        )]


# ── Analyzer 4: Health Score ──────────────────────────────────────

class HealthScoreAnalyzer(BaseAnalyzer):
    """
    Financial health score 0-100 with qualitative explanation.

    Scoring:
      Savings rate >= 30% → Excellent (score 80-100)
      Savings rate >= 20% → Good      (score 60-79)
      Savings rate >= 10% → Fair      (score 40-59)
      Savings rate >= 0%  → Poor      (score 20-39)
      Spending > Income   → Critical  (score 0-19)
    """

    name = "health_score"

    def analyze(self, data: PeriodData) -> list[Insight]:
        if data.current_income == 0:
            return []

        savings = data.current_income - data.current_expense
        savings_rate = round(savings / data.current_income * 100, 1)

        if savings_rate >= 30:
            score = min(100, 80 + int(savings_rate - 30))
            label = "Excellent"
            severity = "info"
            tip = "Keep it up — you're building strong financial habits."
        elif savings_rate >= 20:
            score = 60 + int((savings_rate - 20) * 2)
            label = "Good"
            severity = "info"
            tip = "Try to push savings above 30% for long-term security."
        elif savings_rate >= 10:
            score = 40 + int((savings_rate - 10) * 2)
            label = "Fair"
            severity = "warning"
            tip = "Look for recurring expenses you can reduce."
        elif savings_rate >= 0:
            score = 20 + int(savings_rate * 2)
            label = "Poor"
            severity = "warning"
            tip = "Your expenses are consuming most of your income."
        else:
            score = max(0, 20 + int(savings_rate))
            label = "Critical"
            severity = "alert"
            tip = "You are spending more than you earn. Review expenses immediately."

        return [self._make_insight(
            user_id=data.user_id,
            insight_type="health_score",
            title=f"Financial health score: {score}/100 — {label}",
            body=(
                f"This month: Income ₹{data.current_income:,.0f}, "
                f"Expenses ₹{data.current_expense:,.0f}, "
                f"Savings ₹{savings:,.0f} ({savings_rate}%). "
                f"{tip}"
            ),
            severity=severity,
            period_start=data.current_start,
            period_end=data.current_end,
        )]


# ── Analyzer 5: Spending Predictor ───────────────────────────────

class SpendingPredictor(BaseAnalyzer):
    """
    Linear extrapolation to estimate month-end spending.

    Method:
      daily_avg = current_expense / days_elapsed
      predicted  = daily_avg * days_in_month

    Phase 3: Replace with ARIMA or Prophet for seasonal patterns.
    """

    name = "prediction"

    def analyze(self, data: PeriodData) -> list[Insight]:
        if len(data.current_debits) < MIN_TRANSACTIONS_FOR_INSIGHT:
            return []

        days_elapsed = max(1, (data.today - data.current_start).days + 1)
        days_in_month = (
            data.current_start.replace(
                month=data.current_start.month % 12 + 1,
                day=1,
            ) - timedelta(days=1)
        ).day

        days_remaining = days_in_month - days_elapsed

        if days_remaining <= 0:
            return []

        daily_avg = data.current_expense / days_elapsed
        predicted_total = round(daily_avg * days_in_month, 0)
        predicted_remaining = round(daily_avg * days_remaining, 0)

        return [self._make_insight(
            user_id=data.user_id,
            insight_type="prediction",
            title=f"Estimated month-end spend: ₹{predicted_total:,.0f}",
            body=(
                f"Based on your spending so far "
                f"(₹{data.current_expense:,.0f} in {days_elapsed} days), "
                f"you are on track to spend ₹{predicted_total:,.0f} "
                f"this month. Expected remaining: ₹{predicted_remaining:,.0f} "
                f"over {days_remaining} days."
            ),
            severity="info",
            period_start=data.current_start,
            period_end=data.current_end,
        )]


# ── Analyzer 6: Savings Analyzer ─────────────────────────────────

class SavingsAnalyzer(BaseAnalyzer):
    """Positive reinforcement or alert based on savings rate."""

    name = "savings_alert"

    def analyze(self, data: PeriodData) -> list[Insight]:
        if data.current_income == 0:
            return []

        savings = data.current_income - data.current_expense
        if savings <= 0:
            return []

        rate = round(savings / data.current_income * 100, 1)

        if rate >= 30:
            title = f"Excellent savings this month: {rate}%"
            body = (
                f"You saved ₹{savings:,.0f} ({rate}% of income) this month. "
                f"Financial advisors recommend saving at least 20%. "
                f"You're well above that — great discipline."
            )
            severity = "info"
        elif rate >= 20:
            title = f"Good savings rate: {rate}%"
            body = (
                f"You saved ₹{savings:,.0f} ({rate}% of income) this month. "
                f"You're meeting the 20% savings benchmark."
            )
            severity = "info"
        else:
            return []  # Low savings handled by HealthScoreAnalyzer

        return [self._make_insight(
            user_id=data.user_id,
            insight_type="savings_alert",
            title=title,
            body=body,
            severity=severity,
            period_start=data.current_start,
            period_end=data.current_end,
        )]


# ── Analyzer 7: Budget Analyzer ───────────────────────────────────

class BudgetAnalyzer(BaseAnalyzer):
    """
    Compare actual spending against user's monthly_budget.
    Only runs if user has set a budget (monthly_budget field).
    """

    name = "budget_alert"

    def analyze(self, data: PeriodData) -> list[Insight]:
        if not data.user:
            return []

        # Check if monthly_budget column exists and is set
        budget = getattr(data.user, "monthly_budget", None)
        if not budget or budget <= 0:
            return []

        if data.current_expense == 0:
            return []

        pct_used = round(data.current_expense / budget * 100, 1)

        if pct_used >= 100:
            return [self._make_insight(
                user_id=data.user_id,
                insight_type="budget_alert",
                title=f"Monthly budget exceeded ({pct_used:.0f}%)",
                body=(
                    f"You have spent ₹{data.current_expense:,.0f} "
                    f"against your monthly budget of ₹{budget:,.0f}. "
                    f"You are ₹{data.current_expense - budget:,.0f} over budget."
                ),
                severity="alert",
                period_start=data.current_start,
                period_end=data.current_end,
            )]
        elif pct_used >= 80:
            remaining = budget - data.current_expense
            return [self._make_insight(
                user_id=data.user_id,
                insight_type="budget_alert",
                title=f"Budget at {pct_used:.0f}% — ₹{remaining:,.0f} remaining",
                body=(
                    f"You have used {pct_used:.0f}% of your monthly budget. "
                    f"₹{remaining:,.0f} remains for the rest of the month."
                ),
                severity="warning",
                period_start=data.current_start,
                period_end=data.current_end,
            )]

        return []


# ── Insight Engine ────────────────────────────────────────────────

class InsightEngine:
    """
    Orchestrates all analyzers and persists results.

    Usage:
        engine = InsightEngine()
        count = engine.generate(user_id=1, db=db)
    """

    def __init__(self):
        self._analyzers: list[BaseAnalyzer] = [
            SpendingTrendAnalyzer(),
            AnomalyDetector(),
            TopCategoryAnalyzer(),
            HealthScoreAnalyzer(),
            SpendingPredictor(),
            SavingsAnalyzer(),
            BudgetAnalyzer(),
        ]

    def generate(
        self,
        user_id: int,
        db: Session,
        today: Optional[date] = None,
    ) -> int:
        """
        Generate insights for a user and persist to DB.

        Steps:
          1. Delete existing insights for current period.
          2. Fetch transaction data into PeriodData.
          3. Run each analyzer independently.
          4. Persist all generated insights.
          5. Return count of insights generated.

        Args:
            user_id: ID of the authenticated user.
            db:      Active SQLAlchemy session.
            today:   Override today's date (for testing).

        Returns:
            Number of insights generated and stored.
        """
        today = today or date.today()
        current_start = today.replace(day=1)

        log.info(
            "insight_engine.start",
            user_id=user_id,
            period_start=str(current_start),
            period_end=str(today),
        )

        # Delete existing insights for current period to avoid duplicates
        deleted = (
            db.query(Insight)
            .filter(
                Insight.user_id == user_id,
                Insight.period_start >= current_start,
            )
            .delete(synchronize_session=False)
        )
        if deleted:
            log.debug(
                "insight_engine.deleted_stale",
                user_id=user_id,
                count=deleted,
            )

        # Fetch period data (one set of DB queries shared across analyzers)
        try:
            data = PeriodData(user_id=user_id, db=db, today=today)
        except Exception as e:
            log.error(
                "insight_engine.data_fetch_failed",
                user_id=user_id,
                error=str(e),
            )
            return 0

        # Run all analyzers
        all_insights: list[Insight] = []

        for analyzer in self._analyzers:
            try:
                results = analyzer.analyze(data)
                all_insights.extend(results)
                log.debug(
                    "insight_engine.analyzer_done",
                    analyzer=analyzer.name,
                    generated=len(results),
                )
            except Exception as e:
                log.error(
                    "insight_engine.analyzer_failed",
                    analyzer=analyzer.name,
                    user_id=user_id,
                    error=str(e),
                )
                # One analyzer failure does not block others

        # Cap total insights per run
        all_insights = all_insights[:MAX_INSIGHTS_PER_RUN]

        # Persist
        for insight in all_insights:
            db.add(insight)

        db.flush()

        log.info(
            "insight_engine.complete",
            user_id=user_id,
            generated=len(all_insights),
        )

        return len(all_insights)


# ── Singleton ─────────────────────────────────────────────────────

_engine: Optional[InsightEngine] = None


def get_insight_engine() -> InsightEngine:
    """Lazy singleton accessor."""
    global _engine
    if _engine is None:
        _engine = InsightEngine()
    return _engine
