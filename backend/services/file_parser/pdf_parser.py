"""
backend/services/file_parser/pdf_parser.py
─────────────────────────────────────────────────────────────
PDF bank statement parser using pdfplumber.

Strategy:
  1. Open PDF with pdfplumber
  2. Extract ALL tables from every page (including 1-row tables)
  3. Group rows by column count
  4. Find the header row — a row containing banking keywords
  5. If no header found, detect layout from data patterns
  6. Merge all data rows under the best header
  7. Write to temp CSV
  8. Delegate to CSVParser pipeline for normalization

Handles:
  - Fragmented tables (ICICI credit card PDFs)
  - Missing headers (SBI passbook PDFs)
  - Mixed column counts across pages

Limitation:
  Works on text-based PDFs (digital bank exports).
  Scanned image PDFs require OCR — Phase 3 enhancement.
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

# Keywords that identify a header row in bank statements.
_HEADER_KEYWORDS = {
    "date", "txn date", "tran date", "transaction date",
    "value date", "posting date",
    "debit", "credit", "amount", "balance",
    "narration", "description", "particulars",
    "details", "remarks", "transaction details",
    "withdrawal", "deposit",
}

# Date patterns for detecting date-like cells (expanded for Indian banks)
_DATE_PATTERNS = [
    # dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy
    re.compile(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$"),
    # dd Mon yyyy, dd-Mon-yyyy, dd/Mon/yyyy (e.g. 01 Jan 2025, 01-Jan-25)
    re.compile(r"^\d{1,2}[/\-.\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[/\-.\s]\d{2,4}$", re.IGNORECASE),
    # yyyy-mm-dd (ISO)
    re.compile(r"^\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}$"),
    # dd Mon yy or dd Mon yyyy without separator (e.g. "01Jan2025")
    re.compile(r"^\d{1,2}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\d{2,4}$", re.IGNORECASE),
]

# Synthetic headers for common Indian bank PDF layouts
_SYNTHETIC_HEADERS = {
    7: ["Date", "Value Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    6: ["Date", "Description", "Ref No", "Debit", "Credit", "Balance"],
    5: ["Date", "Description", "Debit", "Credit", "Balance"],
    4: ["Date", "Description", "Amount", "Balance"],
}


class PDFParser:
    """Parse bank statement PDFs by extracting embedded tables."""

    def parse(self, file_path: Path) -> list[dict]:
        log.info("pdf.parse.start", file=file_path.name)

        # Collect ALL rows from ALL tables across ALL pages.
        all_rows: list[list[str | None]] = []

        try:
            with pdfplumber.open(file_path) as pdf:
                log.info("pdf.pages", count=len(pdf.pages))

                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        for row in table:
                            if row and len(row) >= 3:
                                all_rows.append(row)

        except Exception as e:
            log.error("pdf.parse.error", error=str(e))
            raise ValueError(f"PDF parse failed: {e}")

        if not all_rows:
            log.warning("pdf.no_tables_found", file=file_path.name)
            return []

        log.info("pdf.raw_rows_collected", count=len(all_rows))

        # Group rows by column count
        by_col_count: dict[int, list[list[str | None]]] = {}
        for row in all_rows:
            n = len(row)
            if n not in by_col_count:
                by_col_count[n] = []
            by_col_count[n].append(row)

        # Find the header row across all groups
        best_header: list[str | None] | None = None
        best_header_score = 0
        best_col_count = 0

        for col_count, rows in by_col_count.items():
            for row in rows:
                score = self._header_score(row)
                if score > best_header_score:
                    best_header_score = score
                    best_header = row
                    best_col_count = col_count

        use_synthetic = False
        data_rows = []

        # If we found a good header (score >= 2), check if its data is valid
        if best_header is not None and best_header_score >= 2:
            candidate_data = [
                r for r in by_col_count[best_col_count]
                if r is not best_header and self._header_score(r) < 2
            ]
            # Validate: do the data rows contain date-like values?
            date_rows = [r for r in candidate_data if self._row_has_date(r)]

            if date_rows:
                data_rows = candidate_data
                log.info(
                    "pdf.header_detected",
                    header=[str(h).strip()[:30] for h in best_header],
                    data_rows=len(data_rows),
                    col_count=best_col_count,
                )
            else:
                # Header found but no date-bearing data in that group.
                # Fall back to synthetic header for a group that has dates.
                log.info(
                    "pdf.header_no_data",
                    header=[str(h).strip()[:30] for h in best_header],
                    candidate_data=len(candidate_data),
                )
                use_synthetic = True
        else:
            use_synthetic = True

        # Synthetic header fallback: find the column-count group
        # with the most date-bearing rows and assign a known header.
        if use_synthetic:
            best_header = None
            data_rows = []

            for col_count in sorted(by_col_count.keys(), reverse=True):
                rows = by_col_count[col_count]
                # Check if rows contain date-like values in first column
                data_candidate = [
                    r for r in rows
                    if self._row_has_date(r)
                ]
                if len(data_candidate) >= 2:
                    # Use synthetic header for this column count
                    if col_count in _SYNTHETIC_HEADERS:
                        best_header = _SYNTHETIC_HEADERS[col_count]
                        best_col_count = col_count
                        data_rows = data_candidate
                        log.info(
                            "pdf.synthetic_header",
                            col_count=col_count,
                            data_rows=len(data_rows),
                            header=best_header,
                        )
                        break

            if best_header is None:
                log.warning("pdf.no_usable_data", file=file_path.name)
                return []

        if not data_rows:
            return []

        # Clean header: strip whitespace, replace newlines
        clean_header = [
            str(h).replace("\n", " ").strip() if h else f"col_{i}"
            for i, h in enumerate(best_header)
        ]

        df = pd.DataFrame(data_rows, columns=clean_header, dtype=str)
        df = df.dropna(how="all")

        if df.empty:
            return []

        # Write to temp CSV and delegate to CSVParser
        with tempfile.NamedTemporaryFile(
            suffix=".csv",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as tmp:
            df.to_csv(tmp.name, index=False)
            csv_path = Path(tmp.name)

        try:
            parser = CSVParser()
            rows = parser.parse(csv_path)
            return rows
        finally:
            csv_path.unlink(missing_ok=True)

    @staticmethod
    def _header_score(row: list[str | None]) -> int:
        """
        Score a row based on how many cells match banking keywords.
        Higher score = more likely to be the header row.
        """
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
            # Check all date patterns
            for pattern in _DATE_PATTERNS:
                if pattern.match(val):
                    return True
            # Fallback: try dateutil parse for unusual formats
            try:
                from dateutil import parser as dp
                result = dp.parse(val, dayfirst=True, fuzzy=False)
                # Only accept if the string is reasonably short (date-like)
                if len(val) <= 20 and result:
                    return True
            except Exception:
                pass
        return False
