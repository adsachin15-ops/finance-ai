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
    "details", "remarks", "transaction details", "transaction remarks",
    "withdrawal", "deposit", "withdrawals", "deposits",
    "withdrawal amount", "deposit amount",
    "dr", "cr", "closing balance",
    "chq no", "cheque no", "ref no", "reference",
    "ref. number", "s no", "s no.",
}

# ── Date patterns (all Indian bank formats) ──────────────────
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
    # dd-MON-yy (15-SEP-25) — ICICI credit card format
    re.compile(
        r"^\d{1,2}-"
        r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
        r"-\d{2,4}$",
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
]

# Money pattern: captures amounts like 1,234.56 or 1234.56
_MONEY_RE = re.compile(r"[\d,]+\.\d{2}")

# Synthetic headers for common column counts
_SYNTHETIC_HEADERS = {
    8: ["Sl No", "Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    7: ["Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    6: ["Date", "Ref No", "Description", "col_3", "col_4", "Amount"],
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

                for page in pdf.pages:
                    # Method 1: Extract tables
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        for row in table:
                            if row and any(c for c in row if c and str(c).strip()):
                                all_rows.append(row)

                    # Collect raw text for fallback
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

        # Try table-based parsing first (needs at least 3 data rows)
        result = self._parse_from_tables(all_rows)
        if result and len(result) >= 2:
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
            # Collect ALL rows with this column count (across all pages)
            group = by_col[best_col_count]
            data_rows = [
                r for r in group
                if self._header_score(r) < 2
            ]

            # Validate: at least some rows have dates
            date_rows = [r for r in data_rows if self._row_has_date(r)]
            if not date_rows:
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

        # Clean header: strip whitespace, replace newlines
        clean_header = [
            str(h).replace("\n", " ").strip() if h else f"col_{i}"
            for i, h in enumerate(best_header)
        ]

        # Fix ICICI Credit Card: header has empty Date (col_0) and Amount (col_5)
        # Detect: first column data has dates, header says "col_0"
        if clean_header[0].startswith("col_") and data_rows:
            # Check if first column has date-like values
            first_col_dates = sum(
                1 for r in data_rows[:10]
                if r[0] and self._row_has_date([r[0]])
            )
            if first_col_dates >= 3:
                clean_header[0] = "Date"
                log.info("pdf.fix_header.date_col", original="col_0")

        # Fix unnamed last column that contains amounts
        last_idx = len(clean_header) - 1
        if clean_header[last_idx].startswith("col_") and data_rows:
            # Check if last column has money-like values
            money_count = sum(
                1 for r in data_rows[:10]
                if r[last_idx] and _MONEY_RE.search(str(r[last_idx]).replace(",", "").strip())
            )
            if money_count >= 3:
                clean_header[last_idx] = "Amount"
                log.info("pdf.fix_header.amount_col", original=f"col_{last_idx}")

        log.info(
            "pdf.table_parse.header",
            header=[h[:25] for h in clean_header],
            data_rows=len(data_rows),
        )

        return self._to_parsed_rows(data_rows, clean_header)

    # ══════════════════════════════════════════════════════════
    # METHOD 2: Text-line-based extraction (fallback)
    # ══════════════════════════════════════════════════════════

    def _parse_from_text_lines(self, lines: list[str]) -> list[dict]:
        """
        Parse transactions from raw text lines.
        Handles PDFs where tables only contain headers (ICICI savings)
        or have no table structure at all (SBI).

        Special handling for multi-line ICICI format:
          1 30.01.2026 16000.00 55993.42
          UPI/GROWW INVE/groww.rzp.brk@/...
        """
        if not lines:
            return []

        # -----------------------------------------------------------
        # Strategy A: ICICI savings format
        #   "S_No  DD.MM.YYYY  [description_fragment]  amount  balance"
        #   Next line(s) contain rest of description
        # -----------------------------------------------------------
        icici_pattern = re.compile(
            r"^\d+\s+"                         # S.No
            r"(\d{1,2}\.\d{1,2}\.\d{4})\s+"    # Date (dd.mm.yyyy)
            r"(.+?)\s+"                         # Description fragment
            r"([\d,]+\.\d{2})\s+"              # Amount 1
            r"([\d,]+\.\d{2})\s*$"             # Amount 2 (balance)
        )

        # ICICI variant: S_No  DD.MM.YYYY  amount  balance (no inline desc)
        icici_pattern_nodesc = re.compile(
            r"^\d+\s+"                         # S.No
            r"(\d{1,2}\.\d{1,2}\.\d{4})\s+"    # Date
            r"([\d,]+\.\d{2})\s+"              # Amount
            r"([\d,]+\.\d{2})\s*$"             # Balance
        )

        transactions = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # Try ICICI format with inline description
            m = icici_pattern.match(line)
            if m:
                date_str = m.group(1)
                desc_fragment = m.group(2).strip()
                amount1 = m.group(3)
                amount2 = m.group(4)  # Usually the balance

                # Collect continuation lines (description wraps)
                desc_parts = [desc_fragment]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    # Stop if next line is a new transaction or header
                    if icici_pattern.match(next_line) or icici_pattern_nodesc.match(next_line):
                        break
                    if self._extract_date_from_line(next_line):
                        break
                    if next_line.startswith("Transaction") or next_line.startswith("S No"):
                        break
                    if next_line and not next_line[0].isdigit():
                        desc_parts.append(next_line)
                        j += 1
                    else:
                        break

                full_desc = " ".join(desc_parts).strip()
                amt = float(amount1.replace(",", ""))

                # Determine debit/credit from description
                upper = full_desc.upper()
                if any(k in upper for k in ["SALARY", "RECEIVED", "DEPOSIT", "REFUND", "CREDIT", "NEFT CR", "CASHBACK"]):
                    tx = {"date": date_str, "description": full_desc[:500], "debit": 0, "credit": amt}
                else:
                    tx = {"date": date_str, "description": full_desc[:500], "debit": amt, "credit": 0}

                transactions.append(tx)
                i = j
                continue

            # Try ICICI format without inline description
            m2 = icici_pattern_nodesc.match(line)
            if m2:
                date_str = m2.group(1)
                amount1 = m2.group(2)
                amount2 = m2.group(3)

                # Next lines are description
                desc_parts = []
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if icici_pattern.match(next_line) or icici_pattern_nodesc.match(next_line):
                        break
                    if self._extract_date_from_line(next_line):
                        break
                    if next_line.startswith("Transaction") or next_line.startswith("S No"):
                        break
                    if next_line:
                        desc_parts.append(next_line)
                        j += 1
                    else:
                        break

                full_desc = " ".join(desc_parts).strip()
                amt = float(amount1.replace(",", ""))

                upper = full_desc.upper()
                if any(k in upper for k in ["SALARY", "RECEIVED", "DEPOSIT", "REFUND", "CREDIT", "NEFT CR", "CASHBACK"]):
                    tx = {"date": date_str, "description": full_desc[:500], "debit": 0, "credit": amt}
                else:
                    tx = {"date": date_str, "description": full_desc[:500], "debit": amt, "credit": 0}

                transactions.append(tx)
                i = j
                continue

            # -----------------------------------------------------------
            # Strategy B: Generic — line starts with a date
            # -----------------------------------------------------------
            date_match = self._extract_date_from_line(line)
            if date_match:
                remainder = line[len(date_match):].strip()
                amounts = _MONEY_RE.findall(remainder)

                if not amounts and i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if not self._extract_date_from_line(next_line):
                        amounts = _MONEY_RE.findall(next_line)
                        remainder = remainder + " " + next_line.strip()

                if amounts:
                    desc = remainder
                    for amt_str in amounts:
                        desc = desc.replace(amt_str, "").strip()
                    desc = re.sub(r"\s{2,}", " ", desc).strip(" -/|")

                    parsed_amts = []
                    for a in amounts:
                        try:
                            parsed_amts.append(float(a.replace(",", "")))
                        except ValueError:
                            pass

                    if parsed_amts:
                        tx = self._build_text_transaction(date_match, desc, parsed_amts)
                        if tx:
                            transactions.append(tx)

            i += 1

        if not transactions:
            return []

        log.info("pdf.text_parse.found", count=len(transactions))
        df = pd.DataFrame(transactions)
        return self._df_to_parsed_rows(df)

    def _build_text_transaction(self, date_str: str, desc: str, amounts: list[float]) -> dict | None:
        """Build a transaction dict from extracted text data."""
        upper_desc = desc.upper()
        is_credit = any(k in upper_desc for k in [
            "CR", "CREDIT", "DEPOSIT", "RECEIVED", "SALARY", "REFUND",
            "CASHBACK", "REVERSAL", "NEFT CR",
        ])
        is_debit = any(k in upper_desc for k in [
            "DR", "DEBIT", "WITHDRAWAL", "PAYMENT", "PURCHASE",
            "PAID", "TRANSFER",
        ])

        if len(amounts) >= 3:
            # debit, credit, balance
            debit = amounts[0] if amounts[0] > 0 else 0
            credit = amounts[1] if amounts[1] > 0 else 0
            return {"date": date_str, "description": desc[:500], "debit": debit, "credit": credit}
        elif len(amounts) == 2:
            a1, a2 = amounts
            if a2 > a1 * 3:
                # a2 is probably balance
                if is_credit:
                    return {"date": date_str, "description": desc[:500], "debit": 0, "credit": a1}
                else:
                    return {"date": date_str, "description": desc[:500], "debit": a1, "credit": 0}
            else:
                return {"date": date_str, "description": desc[:500], "debit": a1, "credit": a2}
        elif len(amounts) == 1:
            amt = amounts[0]
            if amt < 0:
                return {"date": date_str, "description": desc[:500], "debit": 0, "credit": abs(amt)}
            elif is_credit and not is_debit:
                return {"date": date_str, "description": desc[:500], "debit": 0, "credit": amt}
            else:
                return {"date": date_str, "description": desc[:500], "debit": amt, "credit": 0}

        return None

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
        """Extract a date from the beginning of a text line."""
        line = line.strip()
        if not line:
            return None

        date_regexes = [
            r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}",
            r"\d{1,2}[/\-.\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[/\-.\s]\d{2,4}",
            r"\d{1,2}-(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{2,4}",
            r"\d{1,2}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\d{2,4}",
            r"\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}",
        ]

        for pattern in date_regexes:
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                return m.group(0)

        return None
