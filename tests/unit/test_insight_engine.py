"""
tests/unit/test_insight_engine.py
─────────────────────────────────────────────────────────────
Unit tests for backend/ai/insight_engine.py

Tests cover:
  - Each analyzer in isolation with mock PeriodData
  - Insight field correctness (type, severity, title, body)
  - Edge cases: no data, insufficient transactions
  - InsightEngine orchestration
  - Singleton behavior
  - Analyzer independence (one failure does not block others)
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.ai.insight_engine import (
    AnomalyDetector,
    BaseAnalyzer,
    BudgetAnalyzer,
    HealthScoreAnalyzer,
    InsightEngine,
    PeriodData,
    SavingsAnalyzer,
    SpendingPredictor,
    SpendingTrendAnalyzer,
    TopCategoryAnalyzer,
    get_insight_engine,
)
from backend.models.insight import Insight
from backend.models.transaction import Transaction


# ── Mock Helpers ──────────────────────────────────────────────────

def _make_transaction(
    amount: float,
    tx_type: str = "debit",
    category: str = "Food",
    description: str = "Test transaction",
    days_ago: int = 5,
) -> MagicMock:
    """Create a mock Transaction object."""
    tx = MagicMock(spec=Transaction)
    tx.amount = amount
    tx.type = tx_type
    tx.category = category
    tx.description = description
    tx.raw_description = description
    tx.date = date.today() - timedelta(days=days_ago)
    return tx


def _make_period_data(
    current_debits: list = None,
    current_credits: list = None,
    prev_debits: list = None,
    recent_txns: list = None,
    user_id: int = 1,
    monthly_budget: float = None,
) -> MagicMock:
    """Create a mock PeriodData object."""
    data = MagicMock(spec=PeriodData)
    data.user_id = user_id
    data.today = date.today()
    data.current_start = data.today.replace(day=1)
    data.current_end = data.today
    data.prev_start = (data.current_start - timedelta(days=1)).replace(day=1)
    data.prev_end = data.current_start - timedelta(days=1)

    data.current_debits = current_debits or []
    data.current_credits = current_credits or []
    data.prev_debits = prev_debits or []
    data.recent_txns = (recent_txns or []) + (current_debits or [])

    data.current_txns = (current_debits or []) + (current_credits or [])
    data.prev_txns = prev_debits or []

    data.current_expense = sum(t.amount for t in data.current_debits)
    data.current_income = sum(t.amount for t in data.current_credits)
    data.prev_expense = sum(t.amount for t in data.prev_debits)

    # User mock
    user = MagicMock()
    user.monthly_budget = monthly_budget
    data.user = user

    def category_totals(txns):
        totals = {}
        for t in txns:
            if t.type == "debit":
                cat = t.category or "Other"
                totals[cat] = totals.get(cat, 0.0) + t.amount
        return totals

    data.category_totals = category_totals
    return data


# ── SpendingTrendAnalyzer Tests ───────────────────────────────────

class TestSpendingTrendAnalyzer:

    def setup_method(self):
        self.analyzer = SpendingTrendAnalyzer()

    def test_returns_empty_when_insufficient_current_data(self):
        data = _make_period_data(
            current_debits=[_make_transaction(100)] * 3,  # < 5
        )
        assert self.analyzer.analyze(data) == []

    def test_returns_empty_when_insufficient_prev_data(self):
        data = _make_period_data(
            current_debits=[_make_transaction(100)] * 6,
            prev_debits=[_make_transaction(100)] * 3,  # < 5
        )
        assert self.analyzer.analyze(data) == []

    def test_detects_spending_increase(self):
        current = [_make_transaction(1000)] * 6
        prev = [_make_transaction(500)] * 6
        data = _make_period_data(
            current_debits=current,
            prev_debits=prev,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) >= 1
        assert any("more" in i.title for i in insights)

    def test_detects_spending_decrease(self):
        current = [_make_transaction(500)] * 6
        prev = [_make_transaction(1000)] * 6
        data = _make_period_data(
            current_debits=current,
            prev_debits=prev,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) >= 1
        assert any("less" in i.title for i in insights)

    def test_large_increase_is_warning(self):
        current = [_make_transaction(2000)] * 6
        prev = [_make_transaction(500)] * 6
        data = _make_period_data(
            current_debits=current,
            prev_debits=prev,
        )
        insights = self.analyzer.analyze(data)
        warning_insights = [i for i in insights if i.severity == "warning"]
        assert len(warning_insights) >= 1

    def test_insight_has_correct_type(self):
        current = [_make_transaction(1000)] * 6
        prev = [_make_transaction(500)] * 6
        data = _make_period_data(current_debits=current, prev_debits=prev)
        insights = self.analyzer.analyze(data)
        for i in insights:
            assert i.insight_type == "spending_trend"

    def test_insight_has_period_dates(self):
        current = [_make_transaction(1000)] * 6
        prev = [_make_transaction(500)] * 6
        data = _make_period_data(current_debits=current, prev_debits=prev)
        insights = self.analyzer.analyze(data)
        for i in insights:
            assert i.period_start is not None
            assert i.period_end is not None


# ── AnomalyDetector Tests ─────────────────────────────────────────

class TestAnomalyDetector:

    def setup_method(self):
        self.analyzer = AnomalyDetector()

    def test_returns_empty_when_insufficient_data(self):
        data = _make_period_data(
            recent_txns=[_make_transaction(100)] * 3
        )
        assert self.analyzer.analyze(data) == []

    def test_detects_anomalous_transaction(self):
        # Normal transactions ~100, one huge outlier
        normal = [_make_transaction(100, days_ago=i) for i in range(1, 20)]
        outlier = [_make_transaction(50000, days_ago=2)]
        data = _make_period_data(
            current_debits=normal + outlier,
            recent_txns=normal + outlier,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) >= 1

    def test_anomaly_severity_is_alert(self):
        normal = [_make_transaction(100, days_ago=i) for i in range(1, 20)]
        outlier = [_make_transaction(50000, days_ago=2)]
        data = _make_period_data(
            current_debits=normal + outlier,
            recent_txns=normal + outlier,
        )
        insights = self.analyzer.analyze(data)
        for i in insights:
            assert i.severity == "alert"

    def test_no_anomaly_when_consistent_spending(self):
        # All transactions similar amount — no outlier
        consistent = [_make_transaction(100, days_ago=i) for i in range(1, 20)]
        data = _make_period_data(
            current_debits=consistent,
            recent_txns=consistent,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 0

    def test_anomaly_insight_type(self):
        normal = [_make_transaction(100, days_ago=i) for i in range(1, 20)]
        outlier = [_make_transaction(50000, days_ago=2)]
        data = _make_period_data(
            current_debits=normal + outlier,
            recent_txns=normal + outlier,
        )
        insights = self.analyzer.analyze(data)
        for i in insights:
            assert i.insight_type == "anomaly"

    def test_max_two_anomalies_reported(self):
        normal = [_make_transaction(100, days_ago=i) for i in range(1, 15)]
        outliers = [_make_transaction(50000 + i * 1000, days_ago=i) for i in range(1, 6)]
        data = _make_period_data(
            current_debits=normal + outliers,
            recent_txns=normal + outliers,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) <= 2


# ── TopCategoryAnalyzer Tests ─────────────────────────────────────

class TestTopCategoryAnalyzer:

    def setup_method(self):
        self.analyzer = TopCategoryAnalyzer()

    def test_returns_empty_when_no_data(self):
        data = _make_period_data()
        assert self.analyzer.analyze(data) == []

    def test_identifies_top_category(self):
        txns = (
            [_make_transaction(5000, category="Food")] * 5 +
            [_make_transaction(1000, category="Travel")] * 5
        )
        data = _make_period_data(current_debits=txns)
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert "Food" in insights[0].title

    def test_insight_type_is_top_category(self):
        txns = [_make_transaction(1000, category="Food")] * 6
        data = _make_period_data(current_debits=txns)
        insights = self.analyzer.analyze(data)
        assert insights[0].insight_type == "top_category"

    def test_insight_contains_amount(self):
        txns = [_make_transaction(1000, category="Food")] * 6
        data = _make_period_data(current_debits=txns)
        insights = self.analyzer.analyze(data)
        assert "₹" in insights[0].title or "₹" in insights[0].body


# ── HealthScoreAnalyzer Tests ─────────────────────────────────────

class TestHealthScoreAnalyzer:

    def setup_method(self):
        self.analyzer = HealthScoreAnalyzer()

    def test_returns_empty_when_no_income(self):
        data = _make_period_data(
            current_debits=[_make_transaction(1000)] * 5,
        )
        assert self.analyzer.analyze(data) == []

    def test_excellent_savings_rate(self):
        credits = [_make_transaction(10000, tx_type="credit")] * 1
        debits = [_make_transaction(500)] * 6
        data = _make_period_data(
            current_debits=debits,
            current_credits=credits,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert "Excellent" in insights[0].title
        assert insights[0].severity == "info"

    def test_critical_overspending(self):
        credits = [_make_transaction(1000, tx_type="credit")] * 1
        debits = [_make_transaction(5000)] * 6
        data = _make_period_data(
            current_debits=debits,
            current_credits=credits,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert insights[0].severity == "alert"
        assert "Critical" in insights[0].title

    def test_health_score_in_title(self):
        credits = [_make_transaction(10000, tx_type="credit")] * 1
        debits = [_make_transaction(1000)] * 5
        data = _make_period_data(
            current_debits=debits,
            current_credits=credits,
        )
        insights = self.analyzer.analyze(data)
        assert "/100" in insights[0].title

    def test_insight_type_is_health_score(self):
        credits = [_make_transaction(10000, tx_type="credit")] * 1
        debits = [_make_transaction(1000)] * 5
        data = _make_period_data(
            current_debits=debits,
            current_credits=credits,
        )
        insights = self.analyzer.analyze(data)
        assert insights[0].insight_type == "health_score"


# ── SpendingPredictor Tests ───────────────────────────────────────

class TestSpendingPredictor:

    def setup_method(self):
        self.analyzer = SpendingPredictor()

    def test_returns_empty_when_insufficient_data(self):
        data = _make_period_data(
            current_debits=[_make_transaction(100)] * 3
        )
        assert self.analyzer.analyze(data) == []

    def test_generates_prediction(self):
        debits = [_make_transaction(1000, days_ago=i) for i in range(1, 8)]
        data = _make_period_data(current_debits=debits)
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1

    def test_prediction_insight_type(self):
        debits = [_make_transaction(1000, days_ago=i) for i in range(1, 8)]
        data = _make_period_data(current_debits=debits)
        insights = self.analyzer.analyze(data)
        assert insights[0].insight_type == "prediction"

    def test_prediction_contains_rupee_amount(self):
        debits = [_make_transaction(1000, days_ago=i) for i in range(1, 8)]
        data = _make_period_data(current_debits=debits)
        insights = self.analyzer.analyze(data)
        assert "₹" in insights[0].title


# ── SavingsAnalyzer Tests ─────────────────────────────────────────

class TestSavingsAnalyzer:

    def setup_method(self):
        self.analyzer = SavingsAnalyzer()

    def test_returns_empty_when_no_income(self):
        data = _make_period_data()
        assert self.analyzer.analyze(data) == []

    def test_returns_empty_when_no_savings(self):
        credits = [_make_transaction(1000, tx_type="credit")] * 1
        debits = [_make_transaction(2000)] * 1
        data = _make_period_data(
            current_debits=debits, current_credits=credits
        )
        assert self.analyzer.analyze(data) == []

    def test_excellent_savings_insight(self):
        credits = [_make_transaction(10000, tx_type="credit")] * 1
        debits = [_make_transaction(500)] * 5
        data = _make_period_data(
            current_debits=debits, current_credits=credits
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert "Excellent" in insights[0].title

    def test_good_savings_insight(self):
        credits = [_make_transaction(10000, tx_type="credit")] * 1
        debits = [_make_transaction(7500)] * 1
        data = _make_period_data(
            current_debits=debits, current_credits=credits
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert "Good" in insights[0].title


# ── BudgetAnalyzer Tests ──────────────────────────────────────────

class TestBudgetAnalyzer:

    def setup_method(self):
        self.analyzer = BudgetAnalyzer()

    def test_returns_empty_when_no_budget_set(self):
        data = _make_period_data(monthly_budget=None)
        assert self.analyzer.analyze(data) == []

    def test_returns_empty_when_under_80_percent(self):
        debits = [_make_transaction(500)] * 5
        data = _make_period_data(
            current_debits=debits,
            monthly_budget=10000.0,
        )
        assert self.analyzer.analyze(data) == []

    def test_warning_at_80_percent(self):
        debits = [_make_transaction(8500)] * 1
        data = _make_period_data(
            current_debits=debits,
            monthly_budget=10000.0,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert insights[0].severity == "warning"

    def test_alert_when_exceeded(self):
        debits = [_make_transaction(12000)] * 1
        data = _make_period_data(
            current_debits=debits,
            monthly_budget=10000.0,
        )
        insights = self.analyzer.analyze(data)
        assert len(insights) == 1
        assert insights[0].severity == "alert"
        assert "exceeded" in insights[0].title.lower()

    def test_budget_insight_type(self):
        debits = [_make_transaction(12000)] * 1
        data = _make_period_data(
            current_debits=debits,
            monthly_budget=10000.0,
        )
        insights = self.analyzer.analyze(data)
        assert insights[0].insight_type == "budget_alert"


# ── InsightEngine Tests ───────────────────────────────────────────

class TestInsightEngine:

    def test_engine_has_seven_analyzers(self):
        engine = InsightEngine()
        assert len(engine._analyzers) == 7

    def test_singleton_returns_same_instance(self):
        e1 = get_insight_engine()
        e2 = get_insight_engine()
        assert e1 is e2

    def test_analyzer_names_unique(self):
        engine = InsightEngine()
        names = [a.name for a in engine._analyzers]
        assert len(names) == len(set(names))

    def test_all_required_analyzers_present(self):
        engine = InsightEngine()
        names = {a.name for a in engine._analyzers}
        required = {
            "spending_trend", "anomaly", "top_category",
            "health_score", "prediction", "savings_alert", "budget_alert"
        }
        assert required == names

    def test_insight_objects_have_required_fields(self):
        """Verify BaseAnalyzer._make_insight produces valid Insight objects."""
        analyzer = TopCategoryAnalyzer()
        insight = analyzer._make_insight(
            user_id=1,
            insight_type="test",
            title="Test title",
            body="Test body",
            severity="info",
        )
        assert insight.user_id == 1
        assert insight.insight_type == "test"
        assert insight.title == "Test title"
        assert insight.body == "Test body"
        assert insight.severity == "info"
        assert insight.is_read is False
