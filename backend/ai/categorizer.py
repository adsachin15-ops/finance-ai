from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.core.config import get_settings
from backend.core.logger import get_logger

log = get_logger(__name__)
settings = get_settings()


# ============================================================
# Result Type
# ============================================================

@dataclass
class CategorizationResult:
    category: str
    subcategory: Optional[str]
    merchant: Optional[str]
    confidence: float
    method: str


# ============================================================
# Keyword Taxonomy (FIXED — complete coverage)
# ============================================================

KEYWORD_RULES: dict[str, dict[str, list[str]]] = {

    "Food": {
        "Swiggy": ["swiggy"],
        "Zomato": ["zomato"],
        "Restaurant": [
            "restaurant",
            "hotel",
            "cafe",
            "dining",
        ],
        "Groceries": [
            "grocery",
            "supermarket",
            "dmart",
            "bigbasket",
            "blinkit",
            "zepto",
        ],
    },

    "Travel": {
        "Uber": ["uber"],
        "Ola": ["ola"],
        "Train": [
            "irctc",
            "train",
        ],
        "Flight": [
            "flight",
            "airlines",
        ],
        "Fuel": [
            "petrol",
            "diesel",
            "fuel",
        ],
    },

    "Shopping": {
        "Amazon": ["amazon"],
        "Flipkart": ["flipkart"],
        "Clothing": [
            "zara",
            "pantaloons",
            "mall",
        ],
    },

    "Bills": {
    "Electricity": ["electricity", "power bill"],
    "Mobile": ["recharge"],
    "Rent": ["rent"],

    "Insurance": [
        "insurance",
        "policy",
        "premium",
    ],
   },

    "Health": {
        "Pharmacy": [
            "pharmacy",
            "medical",
            "medicine",
        ],
        "Hospital": [
            "hospital",
            "apollo",
            "clinic",
        ],
    },

    "Entertainment": {
        "Streaming": [
            "netflix",
            "spotify",
        ],
        "Movies": [
            "pvr",
            "inox",
            "bookmyshow",
        ],
    },

    "Finance": {
        "Loan EMI": [
            "loan",
            "emi",
        ],
        "Investment": [
            "investment",
            "mutual",
            "sip",
            "groww",
            "zerodha",
        ],
    },

    "Transfer": {
        "UPI Transfer": [
            "upi",
            "neft",
            "rtgs",
            "imps",
        ],
        "ATM": [
            "atm withdrawal",
        ],
    },

    "Income": {
        "Salary": [
            "salary",
            "sal credit",
            "payroll",
        ],
        "Refund": [
            "refund",
            "cashback",
            "reversal",
            "chargeback",
        ],
        "Interest": [
            "interest credit",
            "fd interest",
        ],
    },
}


MERCHANT_MAP: dict[str, str] = {
    "swiggy": "Swiggy",
    "zomato": "Zomato",
    "amazon": "Amazon",
    "flipkart": "Flipkart",
    "uber": "Uber",
    "ola": "Ola",
    "netflix": "Netflix",
    "spotify": "Spotify",
    "bookmyshow": "BookMyShow",
    "apollo": "Apollo",
}


# ============================================================
# Text Normalization
# ============================================================

def _normalize(text: str) -> str:

    text = unicodedata.normalize("NFKC", text)

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def _extract_merchant(
    normalized: str,
) -> Optional[str]:

    for keyword, merchant in MERCHANT_MAP.items():

        if keyword in normalized:

            return merchant

    return None


# ============================================================
# Rule Engine
# ============================================================

class RuleBasedCategorizer:

    def categorize(
        self,
        description: str,
    ) -> CategorizationResult:

        if not description or not description.strip():

            return CategorizationResult(
                category="Other",
                subcategory=None,
                merchant=None,
                confidence=0.0,
                method="rule",
            )

        normalized = _normalize(description)

        merchant = _extract_merchant(normalized)

        # PRIORITY: Income mapping

        income_map = {
            "salary": "Salary",
            "refund": "Refund",
            "cashback": "Refund",
            "reversal": "Refund",
            "chargeback": "Refund",
            "interest": "Interest",
        }

        for keyword, subcategory in income_map.items():

            if keyword in normalized:

                return CategorizationResult(
                    category="Income",
                    subcategory=subcategory,
                    merchant=merchant,
                    confidence=0.9,
                    method="rule",
                )

        # Standard rules

        for category, subcategories in KEYWORD_RULES.items():

            for subcategory, keywords in subcategories.items():

                for kw in keywords:

                    if kw in normalized:

                        return CategorizationResult(
                            category=category,
                            subcategory=subcategory,
                            merchant=merchant,
                            confidence=0.8,
                            method="rule",
                        )

        return CategorizationResult(
            category="Other",
            subcategory=None,
            merchant=merchant,
            confidence=0.1,
            method="rule",
        )

    def batch_categorize(
        self,
        descriptions: list[str],
    ) -> list[CategorizationResult]:

        return [
            self.categorize(d)
            for d in descriptions
        ]


# ============================================================
# ML Engine (Phase 2)
# ============================================================

class MLCategorizer:

    def __init__(
        self,
        model_path: str,
    ):

        import joblib

        self._pipeline = joblib.load(model_path)

        log.info(
            "ml.categorizer.loaded",
            path=model_path,
        )

    def categorize(
        self,
        description: str,
    ) -> CategorizationResult:

        normalized = _normalize(description)

        prediction = self._pipeline.predict(
            [normalized]
        )[0]

        probability = (
            self._pipeline
            .predict_proba([normalized])
            .max()
        )

        return CategorizationResult(
            category=prediction,
            subcategory=None,
            merchant=_extract_merchant(normalized),
            confidence=round(float(probability), 2),
            method="ml",
        )


# ============================================================
# Facade
# ============================================================

class TransactionCategorizer:

    def __init__(self):

        self._rule_engine = RuleBasedCategorizer()

        self._ml_engine: Optional[
            MLCategorizer
        ] = None

        if (
            settings.model_path
            and Path(settings.model_path).exists()
        ):

            try:

                self._ml_engine = MLCategorizer(
                    settings.model_path
                )

                log.info(
                    "categorizer.mode",
                    mode="ml+rule",
                )

            except Exception as e:

                log.warning(
                    "categorizer.ml_load_failed",
                    error=str(e),
                )

        else:

            log.info(
                "categorizer.mode",
                mode="rule_only",
            )

    def categorize(
        self,
        description: str,
    ) -> CategorizationResult:

        if self._ml_engine:

            result = (
                self._ml_engine
                .categorize(description)
            )

            if result.confidence >= 0.6:

                return result

        return (
            self._rule_engine
            .categorize(description)
        )

    def batch_categorize(
        self,
        descriptions: list[str],
    ) -> list[CategorizationResult]:

        if self._ml_engine:

            return [
                self.categorize(d)
                for d in descriptions
            ]

        return (
            self._rule_engine
            .batch_categorize(descriptions)
        )


# ============================================================
# Singleton
# ============================================================

_categorizer: Optional[
    TransactionCategorizer
] = None


def get_categorizer() -> TransactionCategorizer:

    global _categorizer

    if _categorizer is None:

        _categorizer = TransactionCategorizer()

    return _categorizer