"""
backend/services/file_parser/pdf_parser.py
─────────────────────────────────────────────────────────────
PDF bank statement parser using pdfplumber.

Supports:
  - SBI (savings, FY statements)
  - ICICI (savings + credit card)
  - HDFC (savings + credit card)
  - Axis, Kotak, PNB, BOB, and most Indian banks
  - Any PDF with tabular transaction data

Strategy:
  1. Try table extraction first (structured PDFs)
  2. Fall back to text-line extraction (unstructured PDFs)
  3. Detect header row or assign synthetic headers
  4. Write to temp CSV → delegate to CSVParser

Limitation:
  Scanned image PDFs require OCR (not supported yet).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pandas as pd
import pdfplumber

from backend.services.file_parser.csv_parser import CSVParser
from backend.core.logger import get_logger

log = get_logger(__name__)


# ── Header detection keywords ────────────────────────────────
_HEADER_KEYWORDS = {
    "date", "txn date", "tran date", "transaction date",
    "value date", "posting date", "txn.date", "trans date",
    "debit", "credit", "amount", "balance",
    "narration", "description", "particulars",
    "details", "remarks", "transaction details",
    "withdrawal", "deposit", "withdrawals", "deposits",
    "dr", "cr", "closing balance",
    "chq no", "cheque no", "ref no", "reference",
}

# ── Date patterns (expanded for all Indian banks) ────────────
_DATE_PATTERNS = [
    # dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy
    re.compile(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$"),
    # dd Mon yyyy, dd-Mon-yyyy, dd/Mon/yyyy (01 Jan 2025, 01-Jan-25)
    re.compile(
        r"^\d{1,2}[/\-.\s]"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"[/\-.\s]\d{2,4}$",
        re.IGNORECASE,
    ),
    # yyyy-mm-dd (ISO)
    re.compile(r"^\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}$"),
    # dd Mon yy without separator (01Jan2025)
    re.compile(
        r"^\d{1,2}"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        r"\d{2,4}$",
        re.IGNORECASE,
    ),
    # dd/mm/yy or dd-mm-yy (2 digit year)
    re.compile(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2}$"),
]

# Money pattern: captures amounts like 1,234.56 or 1234.56
_MONEY_RE = re.compile(r"[\d,]+\.\d{2}")

# Synthetic headers for common column counts
_SYNTHETIC_HEADERS = {
    8: ["Sl No", "Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    7: ["Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    6: ["Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    5: ["Date", "Description", "Debit", "Credit", "Balance"],
    4: ["Date", "Description", "Amount", "Balance"],
    3: ["Date", "Description", "Amount"],
}


class PDFParser:
    """Parse bank statement PDFs by extracting embedded tables."""

    def parse(self, file_path: Path) -> list[dict]:
        log.info("pdf.parse.start", file=file_path.name)

        all_rows: list[list[str | None]] = []
        full_text_lines: list[str] = []

        try:
            with pdfplumber.open(file_path) as pdf:
                log.info("pdf.pages", count=len(pdf.pages))

                for page_num, page in enumerate(pdf.pages):
                    # Method 1: Extract tables
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        for row in table:
                            if row and any(c for c in row if c and str(c).strip()):
                                all_rows.append(row)

                    # Also collect raw text for fallback
                    text = page.extract_text()
                    if text:
                        for line in text.split("\n"):
                            line = line.strip()
                            if line:
                                full_text_lines.append(line)

        except Exception as e:
            log.error("pdf.parse.error", error=str(e))
            raise ValueError(f"PDF parse failed: {e}")

        log.info(
            "pdf.extraction_done",
            table_rows=len(all_rows),
            text_lines=len(full_text_lines),
        )

        # Try table-based parsing first
        result = self._parse_from_tables(all_rows)

        if result:
            log.info("pdf.table_parse.success", rows=len(result))
            return result

        # Fallback: text-line-based parsing
        log.info("pdf.fallback_to_text_parsing")
        result = self._parse_from_text_lines(full_text_lines)

        if result:
            log.info("pdf.text_parse.success", rows=len(result))
            return result

        log.warning("pdf.no_data_extracted", file=file_path.name)
        return []

    # ══════════════════════════════════════════════════════════
    # METHOD 1: Table-based extraction
    # ══════════════════════════════════════════════════════════

    def _parse_from_tables(self, all_rows: list[list]) -> list[dict]:
        if not all_rows:
            return []

        # Filter rows with at least 3 non-empty cells
        filtered = []
        for row in all_rows:
            non_empty = sum(
                1 for c in row
                if c and str(c).strip() and str(c).strip().lower() not in ("nan", "none")
            )
            if non_empty >= 3:
                filtered.append(row)

        if not filtered:
            return []

        # Group by column count
        by_col: dict[int, list[list]] = {}
        for row in filtered:
            n = len(row)
            by_col.setdefault(n, []).append(row)

        # Find the best header row
        best_header = None
        best_score = 0
        best_col_count = 0

        for col_count, rows in by_col.items():
            for row in rows:
                score = self._header_score(row)
                if score > best_score:
                    best_score = score
                    best_header = row
                    best_col_count = col_count

        data_rows = []

        if best_header and best_score >= 2:
            # Use detected header
            group = by_col[best_col_count]
            data_rows = [
                r for r in group
                if r is not best_header and self._header_score(r) < 2
            ]

            # Validate: at least some rows have dates
            date_rows = [r for r in data_rows if self._row_has_date(r)]
            if not date_rows:
                # Header found but no date data — try synthetic
                best_header = None

        if best_header is None:
            # Synthetic header fallback
            for col_count in sorted(by_col.keys(), reverse=True):
                rows = by_col[col_count]
                date_rows = [r for r in rows if self._row_has_date(r)]
                if len(date_rows) >= 2 and col_count in _SYNTHETIC_HEADERS:
                    best_header = _SYNTHETIC_HEADERS[col_count]
                    best_col_count = col_count
                    data_rows = date_rows
                    log.info("pdf.synthetic_header", col_count=col_count, rows=len(data_rows))
                    break

        if not best_header or not data_rows:
            return []

        # Clean header
        clean_header = [
            str(h).replace("\n", " ").strip() if h else f"col_{i}"
            for i, h in enumerate(best_header)
        ]

        return self._to_parsed_rows(data_rows, clean_header)

    # ══════════════════════════════════════════════════════════
    # METHOD 2: Text-line-based extraction (fallback)
    # ══════════════════════════════════════════════════════════

    def _parse_from_text_lines(self, lines: list[str]) -> list[dict]:
        """
        Parse transactions from raw text lines.
        Works for PDFs where pdfplumber can't detect table structure
        (common with SBI, some HDFC statements).

        Approach:
          1. Find lines that start with a date
          2. Extract amounts from those lines
          3. Build structured rows
        """
        if not lines:
            return []

        transaction_lines: list[dict] = []

        for i, line in enumerate(lines):
            date_match = self._extract_date_from_line(line)
            if not date_match:
                continue

            date_str = date_match
            remainder = line[len(date_str):].strip()

            # Extract all money amounts from the line
            amounts = _MONEY_RE.findall(remainder)

            if not amounts:
                # Check next line for amounts (multi-line transactions)
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if not self._extract_date_from_line(next_line):
                        amounts = _MONEY_RE.findall(next_line)
                        remainder = remainder + " " + next_line.strip()

            if not amounts:
                continue

            # Remove amounts from the description
            desc = remainder
            for amt in amounts:
                desc = desc.replace(amt, "").strip()
            # Clean up extra spaces and separators
            desc = re.sub(r"\s{2,}", " ", desc).strip(" -/|")

            # Parse amounts
            parsed_amounts = []
            for a in amounts:
                try:
                    parsed_amounts.append(float(a.replace(",", "")))
                except ValueError:
                    pass

            if not parsed_amounts:
                continue

            # Determine debit/credit/balance
            if len(parsed_amounts) >= 3:
                # Likely: debit, credit, balance
                debit = parsed_amounts[0] if parsed_amounts[0] > 0 else 0
                credit = parsed_amounts[1] if parsed_amounts[1] > 0 else 0
                row = {
                    "date": date_str,
                    "description": desc[:500],
                    "debit": debit,
                    "credit": credit,
                }
            elif len(parsed_amounts) == 2:
                # Could be: amount + balance, or debit + credit
                # Heuristic: if one is much larger, it's probably the balance
                a1, a2 = parsed_amounts
                if a2 > a1 * 3:
                    # a1 = transaction amount, a2 = balance
                    # Check description for CR/DR hints
                    upper_desc = desc.upper()
                    if any(k in upper_desc for k in ["CR", "CREDIT", "DEPOSIT", "RECEIVED", "SALARY"]):
                        row = {"date": date_str, "description": desc[:500], "debit": 0, "credit": a1}
                    else:
                        row = {"date": date_str, "description": desc[:500], "debit": a1, "credit": 0}
                else:
                    row = {"date": date_str, "description": desc[:500], "debit": a1, "credit": a2}
            else:
                # Single amount
                amt = parsed_amounts[0]
                upper_desc = desc.upper()
                if any(k in upper_desc for k in ["CR", "CREDIT", "DEPOSIT", "RECEIVED", "SALARY", "REFUND"]):
                    row = {"date": date_str, "description": desc[:500], "debit": 0, "credit": amt}
                elif any(k in upper_desc for k in ["DR", "DEBIT", "WITHDRAWAL", "PAYMENT", "PURCHASE"]):
                    row = {"date": date_str, "description": desc[:500], "debit": amt, "credit": 0}
                else:
                    row = {"date": date_str, "description": desc[:500], "debit": amt, "credit": 0}

            transaction_lines.append(row)

        if not transaction_lines:
            return []

        log.info("pdf.text_parse.transactions_found", count=len(transaction_lines))

        # Convert to DataFrame → temp CSV → CSVParser
        df = pd.DataFrame(transaction_lines)
        return self._df_to_parsed_rows(df)

    # ══════════════════════════════════════════════════════════
    # Shared helpers
    # ══════════════════════════════════════════════════════════

    def _to_parsed_rows(self, data_rows: list[list], header: list[str]) -> list[dict]:
        """Convert table rows + header to parsed transaction dicts via CSVParser."""
        df = pd.DataFrame(data_rows, columns=header, dtype=str)
        df = df.dropna(how="all")
        if df.empty:
            return []
        return self._df_to_parsed_rows(df)

    def _df_to_parsed_rows(self, df: pd.DataFrame) -> list[dict]:
        """Write DataFrame to temp CSV and delegate to CSVParser."""
        if df.empty:
            return []

        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w", encoding="utf-8",
        ) as tmp:
            df.to_csv(tmp.name, index=False)
            csv_path = Path(tmp.name)

        try:
            parser = CSVParser()
            return parser.parse(csv_path)
        finally:
            csv_path.unlink(missing_ok=True)

    @staticmethod
    def _header_score(row: list[str | None]) -> int:
        """Score a row based on how many cells match banking keywords."""
        score = 0
        for cell in row:
            if cell is None:
                continue
            normalized = str(cell).replace("\n", " ").strip().lower()
            if not normalized or normalized in ("nan", "none"):
                continue
            for keyword in _HEADER_KEYWORDS:
                if keyword == normalized or keyword in normalized:
                    score += 1
                    break
        return score

    @staticmethod
    def _row_has_date(row: list[str | None]) -> bool:
        """Check if a row contains a date-like value in the first 3 columns."""
        for cell in row[:3]:
            if cell is None:
                continue
            val = str(cell).strip()
            if not val or val.lower() in ("nan", "none"):
                continue
            for pattern in _DATE_PATTERNS:
                if pattern.match(val):
                    return True
            # Fallback: dateutil
            try:
                from dateutil import parser as dp
                result = dp.parse(val, dayfirst=True, fuzzy=False)
                if len(val) <= 20 and result:
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _extract_date_from_line(line: str) -> str | None:
        """Try to extract a date from the beginning of a text line."""
        line = line.strip()
        if not line:
            return None

        # Try each date pattern at the start of the line
        date_regexes = [
            # dd/mm/yyyy or dd-mm-yyyy or dd.mm.yyyy
            r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}",
            # dd Mon yyyy or dd-Mon-yyyy
            r"\d{1,2}[/\-.\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[/\-.\s]\d{2,4}",
            # dd Mon yy (no separator)
            r"\d{1,2}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\d{2,4}",
            # yyyy-mm-dd
            r"\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}",
        ]

        for pattern in date_regexes:
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                return m.group(0)

        return None
