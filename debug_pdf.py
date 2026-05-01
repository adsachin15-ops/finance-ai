"""Test the updated parser against all local PDFs."""
import sys
sys.path.insert(0, ".")
from pathlib import Path
from backend.services.file_parser.pdf_parser import PDFParser

parser = PDFParser()

files = [
    ("FY2025 (ICICI Credit Card)", "FY2025-261777570995065.pdf"),
    ("OpTransactionHistory (ICICI Savings)", "OpTransactionHistory30-04-2026.pdf-23-14-30.pdf"),
    ("AccountStatement (HDFC/Other)", "AccountStatement_30042026_231922.pdf"),
]

for label, fname in files:
    fpath = Path(fname)
    if not fpath.exists():
        print(f"\n❌ {label}: file not found")
        continue

    print(f"\n{'='*60}")
    print(f"📄 {label}")
    print(f"   File: {fname}")
    print(f"{'='*60}")

    try:
        rows = parser.parse(fpath)
        print(f"   ✅ Parsed: {len(rows)} transactions")
        if rows:
            # Show first 3
            for r in rows[:3]:
                print(f"   → {r['date']}  {r['type']:6s}  ₹{r['amount']:>10,.2f}  {r.get('description','')[:50]}")
            if len(rows) > 3:
                print(f"   ... and {len(rows) - 3} more")
    except Exception as e:
        print(f"   ❌ Error: {e}")
