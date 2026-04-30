"""Quick parser test."""
from pathlib import Path
from backend.services.file_parser.excel_parser import ExcelParser
from backend.services.file_parser.pdf_parser import PDFParser

project = Path(r"c:\Users\sachi\OneDrive\Desktop\JS\finance-ai")

# Test Excel Parser
print("=" * 60)
print("EXCEL PARSER: sbiex_unlocked.xlsx")
print("=" * 60)
try:
    parser = ExcelParser()
    rows = parser.parse(project / "sbiex_unlocked.xlsx")
    print(f"Rows parsed: {len(rows)}")
    for r in rows[:5]:
        desc = r.get("description", "")[:45]
        print(f"  {r['date']} | {r['type']:6} | {r['amount']:>10} | {desc}")
    if len(rows) > 5:
        print(f"  ... and {len(rows) - 5} more rows")
except Exception as e:
    print(f"ERROR: {e}")

print()

# Test PDF Parser
print("=" * 60)
print("PDF PARSER: icici_credit.pdf")
print("=" * 60)
try:
    parser = PDFParser()
    rows = parser.parse(project / "icici_credit.pdf")
    print(f"Rows parsed: {len(rows)}")
    for r in rows[:5]:
        desc = r.get("description", "")[:45]
        print(f"  {r['date']} | {r['type']:6} | {r['amount']:>10} | {desc}")
    if len(rows) > 5:
        print(f"  ... and {len(rows) - 5} more rows")
except Exception as e:
    print(f"ERROR: {e}")

print()

# Test PDF Parser with other files
for pdf_name in ["sbipd_unlocked.pdf", "icicisal.pdf"]:
    pdf_path = project / pdf_name
    if pdf_path.exists():
        print("=" * 60)
        print(f"PDF PARSER: {pdf_name}")
        print("=" * 60)
        try:
            parser = PDFParser()
            rows = parser.parse(pdf_path)
            print(f"Rows parsed: {len(rows)}")
            for r in rows[:3]:
                desc = r.get("description", "")[:45]
                print(f"  {r['date']} | {r['type']:6} | {r['amount']:>10} | {desc}")
            if len(rows) > 3:
                print(f"  ... and {len(rows) - 3} more rows")
        except Exception as e:
            print(f"ERROR: {e}")
        print()
