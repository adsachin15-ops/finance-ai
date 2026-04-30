"""Debug column mapping for ICICI PDF."""
import pandas as pd
from backend.services.file_parser.csv_parser import CSVParser

data = [['13/03/2026', '13047097629', 'Reliance Payment Solut Thane IN', '-7', '', '396.33 CR']]
header = ['Date', 'SerNo.', 'Transaction Details', 'Reward Points', 'Intl.# amount', 'Amount (in`)']
df = pd.DataFrame(data, columns=header, dtype=str)

parser = CSVParser()
df2 = parser._normalize_columns(df)
print("After normalization:", list(df2.columns))
print()
for col in df.columns:
    n = str(col).strip().lower().replace("\n", " ").strip()
    mapped = df2.columns[list(df.columns).index(col)]
    print(f"  orig={col!r:35} -> norm={n!r:35} -> mapped={mapped!r}")
print()

# Show what _extract_amount_and_type does
row = df2.iloc[0]
print("Row data:")
for k, v in row.items():
    print(f"  {k}: {v!r}")

amt, tx_type = parser._extract_amount_and_type(row)
print(f"\nExtracted: amount={amt}, type={tx_type}")
