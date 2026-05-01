from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from dateutil import parser as date_parser

from backend.core.logger import get_logger
from backend.core.security import sanitize_csv_cell

log = get_logger(__name__)


COLUMN_ALIASES: dict[str, str] = {
    "date": "date",
    "txn date": "date",
    "tran date": "date",
    "transaction date": "date",
    "value date": "value_date",
    "posting date": "date",

    "narration": "description",
    "description": "description",
    "particulars": "description",
    "remarks": "description",
    "details": "description",
    "transaction details": "description",

    "debit": "debit",
    "debit amount": "debit",
    "withdrawal": "debit",
    "withdrawal amt": "debit",
    "dr": "debit",

    "credit": "credit",
    "credit amount": "credit",
    "deposit": "credit",
    "deposit amt": "credit",
    "cr": "credit",

    "amount": "amount",

    "balance": "balance",

    "ref no": "reference",
    "reference": "reference",
    "serno": "reference",
    "sr no": "reference",
    "cheque no": "reference",
}


DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",     # 01 Jan 2025
    "%d %b %y",      # 01 Jan 25
    "%d.%m.%Y",      # 01.01.2025 (ICICI savings)
    "%d.%m.%y",      # 01.01.25
    "%d/%b/%Y",      # 01/Jan/2025
    "%d/%b/%y",      # 01/Jan/25
    "%b %d, %Y",     # Jan 01, 2025
    "%d-%B-%Y",      # 01-January-2025
    "%d-%B-%y",      # 01-January-25
]


class CSVParser:

    def parse(self, file_path: Path) -> list[dict]:

        log.info("csv.parse.start", file=file_path.name)

        df = self._read_dataframe(file_path)

        if df is None or df.empty:
            return []

        df = self._normalize_columns(df)

        df = self._drop_garbage_rows(df)

        if "date" not in df.columns:
            raise ValueError("Date column not found")

        rows: list[dict] = []

        for idx, row in df.iterrows():

            parsed = self._parse_row(row, idx)

            if parsed:
                rows.append(parsed)

        log.info("csv.parse.complete", rows=len(rows))

        return rows


    def _read_dataframe(
        self,
        file_path: Path,
    ) -> Optional[pd.DataFrame]:

        encodings = ["utf-8", "latin-1", "cp1252"]

        delimiters = [",", ";", "|", "\t"]

        for encoding in encodings:
            for delimiter in delimiters:

                try:

                    # Read file content to find CSV start
                    with open(file_path, "r", encoding=encoding) as f:
                        lines = f.readlines()

                    # Skip lines until we find one that looks like CSV headers
                    csv_start = 0
                    header_keywords = ["date", "amount", "balance", "description", "narration"]

                    for i, line in enumerate(lines):
                        line_lower = line.lower().strip()
                        if delimiter in line and any(keyword in line_lower for keyword in header_keywords):
                            csv_start = i
                            break

                    # Read CSV from the detected start line
                    df = pd.read_csv(
                        file_path,
                        encoding=encoding,
                        sep=delimiter,
                        engine="python",
                        on_bad_lines="skip",
                        dtype=str,
                        skip_blank_lines=True,
                        skipinitialspace=True,
                        skiprows=csv_start,
                    )

                    if df.shape[1] >= 2:
                        return df

                except Exception:
                    continue

        return None


    def _normalize_columns(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        renamed: dict[str, str] = {}

        def _clean(col: str) -> str:
            return str(col).strip().lower().replace("\n", " ").replace("\t", " ").strip()

        # Pass 1: Exact matches (highest priority).
        for col in df.columns:
            normalized = _clean(col)
            for alias, canonical in COLUMN_ALIASES.items():
                if alias == normalized:
                    if canonical not in renamed.values():
                        renamed[col] = canonical
                        break

        # Pass 2: Prefix matches — column name starts with the alias.
        # e.g. "amount (in `)" starts with "amount"
        for col in df.columns:
            if col in renamed:
                continue
            normalized = _clean(col)
            for alias, canonical in COLUMN_ALIASES.items():
                if normalized.startswith(alias):
                    if canonical not in renamed.values():
                        renamed[col] = canonical
                        break

        # Pass 3: Substring matches (loosest, fallback).
        # e.g. "intl.# amount" contains "amount"
        for col in df.columns:
            if col in renamed:
                continue
            normalized = _clean(col)
            for alias, canonical in COLUMN_ALIASES.items():
                if alias in normalized:
                    if canonical not in renamed.values():
                        renamed[col] = canonical
                        break

        return df.rename(columns=renamed)


    def _drop_garbage_rows(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        # Remove completely empty rows
        df = df.dropna(how="all")

        # If date column exists, keep only rows with parseable dates
        if "date" in df.columns:

            def is_valid_date(val):
                try:
                    _parse_date(str(val))
                    return True
                except Exception:
                    return False

            mask = df["date"].apply(is_valid_date)
            df = df[mask]

        else:
            # No date column — keep rows that have any real value
            def has_any_value(row):
                for val in row:
                    if val is None:
                        continue
                    s = str(val).strip()
                    if s and s.lower() not in ("nan", "none"):
                        return True
                return False

            df = df[df.apply(has_any_value, axis=1)]

        return df.reset_index(drop=True)


    def _parse_row(
        self,
        row: pd.Series,
        idx: int,
    ) -> Optional[dict]:

        try:

            raw_date = str(row.get("date", "")).strip()

            # Only skip if truly empty
            if not raw_date or raw_date.lower() in ("nan", "none"):
                return None

            try:
                parsed_date = _parse_date(raw_date)

            except Exception:
                # fallback keeps row instead of dropping
                parsed_date = date.today()

            amount, tx_type = self._extract_amount_and_type(row)

            if amount is None:
                return None

            desc = str(row.get("description", "")).strip()

            desc = sanitize_csv_cell(desc)

            clean_desc = _clean_description(desc)

            ref = str(row.get("reference", "")).strip()

            return {
                "date": parsed_date,
                "amount": round(amount, 2),
                "type": tx_type,
                "description": clean_desc,
                "raw_description": desc,
                "notes": ref if ref else None,
            }

        except Exception as e:

            log.debug(
                "csv.row.skip",
                row_idx=idx,
                reason=str(e),
            )

            return None


    def _extract_amount_and_type(
        self,
        row: pd.Series,
    ) -> tuple[Optional[float], str]:

        debit = _safe_float(row.get("debit"))
        credit = _safe_float(row.get("credit"))

        if debit is not None and debit > 0:
            return debit, "debit"

        if credit is not None and credit > 0:
            return credit, "credit"

        # Handle single "amount" column, which may have CR/DR suffix
        raw_amount = str(row.get("amount", "")).strip()
        amount = _safe_float(raw_amount)

        if amount is not None:

            if amount == 0:
                return None, "debit"

            # Detect CR/DR suffix to determine type
            upper = raw_amount.upper()
            if upper.endswith("CR") or " CR" in upper:
                return abs(amount), "credit"
            if upper.endswith("DR") or " DR" in upper:
                return abs(amount), "debit"

            if amount < 0:
                return abs(amount), "debit"

            return amount, "credit"

        return None, "debit"


# helpers


def _parse_date(raw: str) -> date:

    raw = str(raw).strip()

    if not raw:
        raise ValueError("Empty date")

    if raw.lower() in ("nan", "none"):
        raise ValueError("Empty date")

    for fmt in DATE_FORMATS:

        try:
            return pd.to_datetime(
                raw,
                format=fmt,
                errors="raise",
            ).date()

        except Exception:
            continue

    return date_parser.parse(
        raw,
        dayfirst=True,
        fuzzy=True,
    ).date()


def _safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    s = str(value).strip()

    if not s or s.lower() in ("nan", "none", "-"):
        return None

    # Strip currency symbols, commas, whitespace
    s = re.sub(r"[₹$€£,\s]", "", s)

    # Strip CR/DR suffixes (common in Indian bank statements)
    s = re.sub(r"(CR|DR|cr|dr)$", "", s).strip()

    # Handle backtick as rupee symbol (ICICI PDFs use ` for ₹)
    s = s.replace("`", "")

    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]

    try:
        return float(s)

    except Exception:
        return None


def _clean_description(raw: str) -> str:

    if not raw:
        return ""

    if str(raw).strip().lower() == "nan":
        return ""

    cleaned = re.sub(
        r"^(UPI[-/]|NEFT[-/]|RTGS[-/]|IMPS[-/]|POS[-/])",
        "",
        raw,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.strip()

    return cleaned[:500]