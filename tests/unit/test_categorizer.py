"""
tests/unit/test_categorizer.py
─────────────────────────────────────────────────────────────
Unit tests for backend/ai/categorizer.py

Tests cover:
  - Rule-based categorization accuracy
  - Merchant extraction
  - Text normalization
  - Batch categorization
  - Empty/None input handling
  - Confidence score ranges
  - Fallback to "Other" category
  - Category taxonomy correctness
"""

from __future__ import annotations

import pytest

from backend.ai.categorizer import (
    CategorizationResult,
    RuleBasedCategorizer,
    TransactionCategorizer,
    _normalize,
    _extract_merchant,
    get_categorizer,
)


# ── Text Normalization Tests ──────────────────────────────────────

class TestNormalize:

    def test_lowercase(self):
        assert _normalize("SWIGGY ORDER") == "swiggy order"

    def test_removes_special_chars(self):
        result = _normalize("UPI/SWIGGY@123")
        assert "@" not in result
        assert "/" not in result

    def test_collapses_whitespace(self):
        result = _normalize("  hello   world  ")
        assert result == "hello world"

    def test_unicode_normalization(self):
        # Should not crash on unicode
        result = _normalize("₹500 Swiggy")
        assert "swiggy" in result

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_numbers_preserved(self):
        result = _normalize("ORDER 12345")
        assert "12345" in result


# ── Merchant Extraction Tests ─────────────────────────────────────

class TestMerchantExtraction:

    def test_swiggy_extracted(self):
        assert _extract_merchant("swiggy order food") == "Swiggy"

    def test_zomato_extracted(self):
        assert _extract_merchant("zomato delivery") == "Zomato"

    def test_amazon_extracted(self):
        assert _extract_merchant("amazon purchase") == "Amazon"

    def test_uber_extracted(self):
        assert _extract_merchant("uber cab ride") == "Uber"

    def test_netflix_extracted(self):
        assert _extract_merchant("netflix subscription") == "Netflix"

    def test_unknown_returns_none(self):
        assert _extract_merchant("random unknown merchant xyz") is None

    def test_case_insensitive(self):
        assert _extract_merchant("SWIGGY ORDER") is None  # normalized input expected
        assert _extract_merchant("swiggy order") == "Swiggy"


# ── Rule-Based Categorizer Tests ──────────────────────────────────

class TestRuleBasedCategorizer:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cat = RuleBasedCategorizer()

    # Food category
    def test_swiggy_food(self):
        r = self.cat.categorize("SWIGGY ORDER 98234")
        assert r.category == "Food"
        assert r.subcategory == "Swiggy"
        assert r.merchant == "Swiggy"

    def test_zomato_food(self):
        r = self.cat.categorize("Zomato food delivery")
        assert r.category == "Food"
        assert r.subcategory == "Zomato"

    def test_restaurant_food(self):
        r = self.cat.categorize("Cafe Coffee Day bill")
        assert r.category == "Food"

    def test_grocery_food(self):
        r = self.cat.categorize("BigBasket order")
        assert r.category == "Food"
        assert r.subcategory == "Groceries"

    # Travel category
    def test_uber_travel(self):
        r = self.cat.categorize("UBER TRIP MUMBAI")
        assert r.category == "Travel"
        assert r.subcategory == "Uber"

    def test_irctc_travel(self):
        r = self.cat.categorize("IRCTC TICKET BOOKING")
        assert r.category == "Travel"
        assert r.subcategory == "Train"

    def test_fuel_travel(self):
        r = self.cat.categorize("BPCL PETROL PUMP")
        assert r.category == "Travel"
        assert r.subcategory == "Fuel"

    def test_flight_travel(self):
        r = self.cat.categorize("INDIGO AIRLINES BOOKING")
        assert r.category == "Travel"
        assert r.subcategory == "Flight"

    # Shopping category
    def test_amazon_shopping(self):
        r = self.cat.categorize("AMAZON.IN PURCHASE")
        assert r.category == "Shopping"
        assert r.subcategory == "Amazon"

    def test_flipkart_shopping(self):
        r = self.cat.categorize("Flipkart order payment")
        assert r.category == "Shopping"
        assert r.subcategory == "Flipkart"

    # Bills category
    def test_electricity_bill(self):
        r = self.cat.categorize("BESCOM ELECTRICITY BILL")
        assert r.category == "Bills"
        assert r.subcategory == "Electricity"

    def test_rent_bill(self):
        r = self.cat.categorize("House rent payment")
        assert r.category == "Bills"
        assert r.subcategory == "Rent"

    def test_insurance_bill(self):
        r = self.cat.categorize("LIC premium payment")
        assert r.category == "Bills"
        assert r.subcategory == "Insurance"

    # Health category
    def test_pharmacy(self):
        r = self.cat.categorize("Apollo Pharmacy purchase")
        assert r.category == "Health"
        assert r.subcategory == "Pharmacy"

    def test_hospital(self):
        r = self.cat.categorize("Apollo Hospital consultation")
        assert r.category == "Health"

    # Entertainment category
    def test_netflix_entertainment(self):
        r = self.cat.categorize("Netflix subscription monthly")
        assert r.category == "Entertainment"
        assert r.subcategory == "Streaming"

    def test_bookmyshow(self):
        r = self.cat.categorize("BookMyShow movie ticket")
        assert r.category == "Entertainment"
        assert r.subcategory == "Movies"

    # Finance category
    def test_loan_emi(self):
        r = self.cat.categorize("Home loan EMI payment")
        assert r.category == "Finance"
        assert r.subcategory == "Loan EMI"

    def test_investment(self):
        r = self.cat.categorize("Groww mutual fund SIP")
        assert r.category == "Finance"
        assert r.subcategory == "Investment"

    # Income category
    def test_salary(self):
        r = self.cat.categorize("Salary credit for March")
        assert r.category == "Income"
        assert r.subcategory == "Salary"

    def test_refund(self):
        r = self.cat.categorize("Amazon refund credit")
        assert r.category == "Income"
        assert r.subcategory == "Refund"

    # Transfer category
    def test_upi_transfer(self):
        r = self.cat.categorize("UPI transfer to friend")
        assert r.category == "Transfer"

    def test_atm_withdrawal(self):
        r = self.cat.categorize("ATM withdrawal HDFC")
        assert r.category == "Transfer"
        assert r.subcategory == "ATM"

    # Fallback
    def test_unknown_falls_back_to_other(self):
        r = self.cat.categorize("XYZ123 RANDOM UNKNOWN TXN")
        assert r.category == "Other"
        assert r.subcategory is None

    def test_empty_string_returns_other(self):
        r = self.cat.categorize("")
        assert r.category == "Other"
        assert r.confidence == 0.0

    def test_whitespace_only_returns_other(self):
        r = self.cat.categorize("   ")
        assert r.category == "Other"

    # Confidence scores
    def test_known_category_confidence_above_0_5(self):
        r = self.cat.categorize("Swiggy food order")
        assert r.confidence > 0.5

    def test_unknown_category_low_confidence(self):
        r = self.cat.categorize("XYZ RANDOM TXN 999")
        assert r.confidence <= 0.2

    def test_confidence_between_0_and_1(self):
        for desc in ["Swiggy", "Uber", "Netflix", "Amazon", "Unknown XYZ"]:
            r = self.cat.categorize(desc)
            assert 0.0 <= r.confidence <= 1.0

    # Method field
    def test_method_is_rule(self):
        r = self.cat.categorize("Swiggy order")
        assert r.method == "rule"

    # Return type
    def test_returns_categorization_result(self):
        r = self.cat.categorize("Swiggy")
        assert isinstance(r, CategorizationResult)


# ── Batch Categorization Tests ────────────────────────────────────

class TestBatchCategorization:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cat = RuleBasedCategorizer()

    def test_batch_returns_same_count(self):
        descriptions = ["Swiggy", "Uber", "Amazon", "Unknown XYZ"]
        results = self.cat.batch_categorize(descriptions)
        assert len(results) == len(descriptions)

    def test_batch_empty_list(self):
        results = self.cat.batch_categorize([])
        assert results == []

    def test_batch_results_match_individual(self):
        descriptions = ["Swiggy order", "IRCTC ticket", "Netflix"]
        batch = self.cat.batch_categorize(descriptions)
        individual = [self.cat.categorize(d) for d in descriptions]
        for b, i in zip(batch, individual):
            assert b.category == i.category
            assert b.subcategory == i.subcategory

    def test_batch_all_return_categorization_result(self):
        results = self.cat.batch_categorize(["Swiggy", "Uber"])
        for r in results:
            assert isinstance(r, CategorizationResult)


# ── TransactionCategorizer Facade Tests ───────────────────────────

class TestTransactionCategorizer:

    def test_singleton_returns_same_instance(self):
        c1 = get_categorizer()
        c2 = get_categorizer()
        assert c1 is c2

    def test_facade_categorizes_correctly(self):
        cat = TransactionCategorizer()
        r = cat.categorize("Swiggy food delivery")
        assert r.category == "Food"

    def test_facade_batch_categorizes(self):
        cat = TransactionCategorizer()
        results = cat.batch_categorize(["Swiggy", "Uber", "Unknown"])
        assert len(results) == 3
        assert results[0].category == "Food"
        assert results[1].category == "Travel"
        assert results[2].category == "Other"
