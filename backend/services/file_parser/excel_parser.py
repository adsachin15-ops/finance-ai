"""
backend/services/file_parser/excel_parser.py
─────────────────────────────────────────────────────────────
Excel (.xlsx/.xls) parser.

Strategy:
  1. Open with openpyxl via pandas ExcelFile
  2. Try each sheet — use the one with the most data rows
  3. Detect the real header row (skip bank metadata rows)
  4. Write to temp CSV with proper headers
  5. Delegate to CSVParser pipeline for normalization

This reuses all column detection, date parsing, and amount
normalization logic already built in CSVParser.

Header detection:
  Indian bank Excel exports commonly have 10-20 rows of metadata
  (customer name, address, branch, balance, IFSC, etc.) before
  the actual data header row. We scan for a row containing at
  least 2 of the banking keywords (date, debit, credit, amount,
  balance, narration, description, particulars) to find the
  real header row.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from backend.services.file_parser.csv_parser import CSVParser
from backend.core.logger import get_logger

log = get_logger(__name__)

# Keywords that identify a header row in bank statements.
# A row must contain at least 2 of these to qualify.
_HEADER_KEYWORDS = {
    "date", "txn date", "tran date", "transaction date",
    "value date", "posting date",
    "debit", "credit", "amount", "balance",
    "narration", "description", "particulars",
    "details", "remarks", "withdrawal", "deposit",
    "ref no", "cheque no", "reference",
}


class ExcelParser:
    """Parse Excel bank statements (.xlsx / .xls)."""

    def parse(self, file_path: Path) -> list[dict]:
        log.info("excel.parse.start", file=file_path.name)

        try:
            xl = pd.ExcelFile(file_path, engine="openpyxl")
            log.info("excel.sheets", sheets=xl.sheet_names)

            # Use the sheet with the most rows
            best_df = None
            for sheet in xl.sheet_names:
                df = xl.parse(sheet, dtype=str, header=None)
                if best_df is None or len(df) > len(best_df):
                    best_df = df

            if best_df is None or best_df.empty:
                log.warning("excel.parse.empty", file=file_path.name)
                return []

            # Detect the real header row
            header_row = self._find_header_row(best_df)

            if header_row is not None:
                log.info(
                    "excel.header_detected",
                    row=header_row,
                    columns=list(best_df.iloc[header_row].values),
                )
                # Use the detected row as the header
                new_header = best_df.iloc[header_row].astype(str).str.strip()
                best_df = best_df.iloc[header_row + 1:].reset_index(drop=True)
                best_df.columns = new_header
            else:
                log.info("excel.header_detection.fallback")

            # Write to temp CSV and delegate to CSVParser
            with tempfile.NamedTemporaryFile(
                suffix=".csv",
                delete=False,
                mode="w",
                encoding="utf-8",
            ) as tmp:
                best_df.to_csv(tmp.name, index=False)
                csv_path = Path(tmp.name)

            try:
                parser = CSVParser()
                rows = parser.parse(csv_path)
                return rows
            finally:
                csv_path.unlink(missing_ok=True)

        except Exception as e:
            log.error(
                "excel.parse.error",
                file=file_path.name,
                error=str(e),
            )
            raise ValueError(f"Excel parse failed: {e}")

    @staticmethod
    def _find_header_row(df: pd.DataFrame) -> int | None:
        """
        Scan rows to find the real header row.

        Returns the row index of the header, or None if none found.
        A row qualifies as a header if it contains at least 2 cells
        whose lowercased text matches any banking keyword.
        """
        # Only scan the first 30 rows — headers are never deeper
        scan_limit = min(len(df), 30)

        for i in range(scan_limit):
            row_values = df.iloc[i].astype(str).str.strip().str.lower()
            matches = 0
            for cell in row_values:
                if cell in ("nan", "none", ""):
                    continue
                # Check exact match or substring match
                for keyword in _HEADER_KEYWORDS:
                    if keyword == cell or keyword in cell:
                        matches += 1
                        break
            if matches >= 2:
                return i

        return None
