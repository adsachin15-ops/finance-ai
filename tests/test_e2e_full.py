"""
Full End-to-End System Test for Finance-AI
==========================================
Steps 2-9: Health, Guest Auth, Core APIs, PDF Upload, Excel Upload,
           Duplicate Detection, Reports, Transaction Verification.
"""
import httpx
import sys
import os
import json

BASE = "http://127.0.0.1:8000"
API  = f"{BASE}/api/v1"
PROJECT = r"c:\Users\sachi\OneDrive\Desktop\JS\finance-ai"

results = {}

def report(step, name, passed, detail=""):
    icon = "PASS" if passed else "FAIL"
    results[step] = {"name": name, "passed": passed, "detail": detail}
    print(f"  [{icon}] {name}")
    if detail:
        for line in detail.strip().split("\n"):
            print(f"         {line}")


# ============================================================
# STEP 2: Health Check
# ============================================================
print("")
print("=" * 60)
print("STEP 2: Server Health Check")
print("=" * 60)
try:
    r = httpx.get(f"{BASE}/health", timeout=10)
    data = r.json()
    ok = r.status_code == 200 and data.get("status") == "healthy"
    report("step2", "GET /health", ok,
           f"status={data.get('status')}, version={data.get('version')}, "
           f"db_reg={data.get('database',{}).get('registered')}, "
           f"db_guest={data.get('database',{}).get('guest')}")
except Exception as e:
    report("step2", "GET /health", False, str(e))
    print("\nServer not running. Aborting.")
    sys.exit(1)


# ============================================================
# STEP 3: Guest Session
# ============================================================
print("")
print("=" * 60)
print("STEP 3: Create Guest Session")
print("=" * 60)
r = httpx.post(f"{API}/auth/guest", json={}, timeout=10)
data = r.json()
ok = r.status_code == 200 and data.get("is_guest") is True
token = data.get("access_token", "")
report("step3", "POST /auth/guest", ok,
       f"status={r.status_code}, is_guest={data.get('is_guest')}, "
       f"display_name={data.get('display_name')}, "
       f"token={token[:25]}...")

if not ok or not token:
    print("\nGuest login failed. Aborting.")
    sys.exit(1)

headers = {"Authorization": f"Bearer {token}"}


# ============================================================
# STEP 4: Test Core APIs
# ============================================================
print("")
print("=" * 60)
print("STEP 4: Test Core APIs (GET endpoints)")
print("=" * 60)
endpoints = [
    ("GET", "/accounts/"),
    ("GET", "/dashboard/summary?period=monthly"),
    ("GET", "/dashboard/trend?granularity=monthly&months=6"),
    ("GET", "/dashboard/heatmap?days=90"),
    ("GET", "/transactions/"),
    ("GET", "/insights/"),
    ("GET", "/upload/logs"),
]
step4_all_pass = True
for method, path in endpoints:
    r = httpx.request(method, f"{API}{path}", headers=headers, timeout=10)
    ok = r.status_code == 200
    if not ok:
        step4_all_pass = False
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:100]
        report("step4_" + path, f"{method} {path}", False, f"status={r.status_code} detail={detail}")
    else:
        report("step4_" + path, f"{method} {path}", True, f"status={r.status_code}")


# ============================================================
# Create accounts for uploads
# ============================================================
print("")
print("-" * 60)
print("Creating test accounts...")
print("-" * 60)

r = httpx.post(f"{API}/accounts/", headers=headers, json={
    "nickname": "ICICI Credit Card",
    "account_type": "credit_card",
    "bank_name": "ICICI",
    "current_balance": 0
}, timeout=10)
icici_id = r.json().get("id") if r.status_code == 201 else None
report("acct_icici", "Create ICICI account", r.status_code == 201,
       f"status={r.status_code}, id={icici_id}")

r = httpx.post(f"{API}/accounts/", headers=headers, json={
    "nickname": "SBI Savings",
    "account_type": "savings",
    "bank_name": "SBI",
    "current_balance": 0
}, timeout=10)
sbi_id = r.json().get("id") if r.status_code == 201 else None
report("acct_sbi", "Create SBI account", r.status_code == 201,
       f"status={r.status_code}, id={sbi_id}")


# ============================================================
# STEP 5: Upload PDF file (icici_credit.pdf)
# ============================================================
print("")
print("=" * 60)
print("STEP 5: Upload PDF — icici_credit.pdf")
print("=" * 60)
pdf_path = os.path.join(PROJECT, "icici_credit.pdf")
if icici_id and os.path.exists(pdf_path):
    with open(pdf_path, "rb") as f:
        r = httpx.post(
            f"{API}/upload/file",
            headers=headers,
            files={"file": ("icici_credit.pdf", f, "application/pdf")},
            data={"account_id": str(icici_id)},
            timeout=120,
        )
    if r.status_code == 200:
        data = r.json()
        pdf_inserted = data.get("records_inserted", 0)
        report("step5", "Upload icici_credit.pdf", pdf_inserted > 0,
               f"status={r.status_code}, parsed={data.get('records_parsed')}, "
               f"inserted={pdf_inserted}, duplicate={data.get('records_duplicate')}, "
               f"failed={data.get('records_failed')}, time={data.get('processing_time_ms')}ms")
    else:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:
            detail = r.text[:200]
        report("step5", "Upload icici_credit.pdf", False,
               f"status={r.status_code}, detail={detail}")
        pdf_inserted = 0
else:
    report("step5", "Upload icici_credit.pdf", False,
           f"account_id={icici_id}, file_exists={os.path.exists(pdf_path)}")
    pdf_inserted = 0


# ============================================================
# STEP 6: Upload Excel file (sbiex_unlocked.xlsx)
# ============================================================
print("")
print("=" * 60)
print("STEP 6: Upload Excel — sbiex_unlocked.xlsx")
print("=" * 60)
xlsx_path = os.path.join(PROJECT, "sbiex_unlocked.xlsx")
if sbi_id and os.path.exists(xlsx_path):
    with open(xlsx_path, "rb") as f:
        r = httpx.post(
            f"{API}/upload/file",
            headers=headers,
            files={"file": ("sbiex_unlocked.xlsx", f,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"account_id": str(sbi_id)},
            timeout=120,
        )
    if r.status_code == 200:
        data = r.json()
        xlsx_inserted = data.get("records_inserted", 0)
        report("step6", "Upload sbiex_unlocked.xlsx", xlsx_inserted > 0,
               f"status={r.status_code}, parsed={data.get('records_parsed')}, "
               f"inserted={xlsx_inserted}, duplicate={data.get('records_duplicate')}, "
               f"failed={data.get('records_failed')}, time={data.get('processing_time_ms')}ms")
    else:
        try:
            detail = r.json().get("detail", r.text[:200])
        except Exception:
            detail = r.text[:200]
        report("step6", "Upload sbiex_unlocked.xlsx", False,
               f"status={r.status_code}, detail={detail}")
        xlsx_inserted = 0
else:
    report("step6", "Upload sbiex_unlocked.xlsx", False,
           f"account_id={sbi_id}, file_exists={os.path.exists(xlsx_path)}")
    xlsx_inserted = 0


# ============================================================
# STEP 7: Duplicate Detection (re-upload same Excel)
# ============================================================
print("")
print("=" * 60)
print("STEP 7: Duplicate Detection — re-upload sbiex_unlocked.xlsx")
print("=" * 60)
if sbi_id and os.path.exists(xlsx_path):
    with open(xlsx_path, "rb") as f:
        r = httpx.post(
            f"{API}/upload/file",
            headers=headers,
            files={"file": ("sbiex_unlocked.xlsx", f,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"account_id": str(sbi_id)},
            timeout=120,
        )
    # Expect 409 Conflict (file-level dedup via hash) or 200 with records_duplicate > 0
    if r.status_code == 409:
        detail = r.json().get("detail", "")
        report("step7", "Duplicate detection (file-level)", True,
               f"status=409 (correctly rejected), detail={detail}")
    elif r.status_code == 200:
        data = r.json()
        dupes = data.get("records_duplicate", 0)
        ins = data.get("records_inserted", 0)
        ok = dupes > 0 and ins == 0
        report("step7", "Duplicate detection (row-level)", ok,
               f"status=200, inserted={ins}, duplicate={dupes}")
    else:
        report("step7", "Duplicate detection", False,
               f"status={r.status_code}, body={r.text[:200]}")
else:
    report("step7", "Duplicate detection", False, "Skipped — missing account or file")


# ============================================================
# STEP 8: Reports
# ============================================================
print("")
print("=" * 60)
print("STEP 8: Report Generation")
print("=" * 60)
report_endpoints = [
    ("/reports/csv", "text/csv"),
    ("/reports/summary/csv", "text/csv"),
    ("/reports/excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
]
for path, expected_type in report_endpoints:
    r = httpx.get(f"{API}{path}", headers=headers, timeout=30)
    ok = r.status_code == 200
    ct = r.headers.get("content-type", "")
    size = len(r.content)
    report(f"step8_{path}", f"GET {path}", ok,
           f"status={r.status_code}, content-type={ct[:50]}, size={size} bytes")


# ============================================================
# STEP 9: Verify Transactions in DB
# ============================================================
print("")
print("=" * 60)
print("STEP 9: Verify Transactions")
print("=" * 60)
r = httpx.get(f"{API}/transactions/", headers=headers, timeout=10)
if r.status_code == 200:
    data = r.json()
    if isinstance(data, list):
        txns = data
        total = len(data)
    elif isinstance(data, dict):
        txns = data.get("transactions", data.get("items", []))
        total = data.get("total", data.get("count", len(txns)))
    else:
        txns = []
        total = 0

    report("step9", f"GET /transactions/ (total={total})", total > 0,
           f"total_records={total}, page_items={len(txns)}")
    if txns:
        print("         Sample transactions:")
        for t in txns[:5]:
            desc = (t.get("description") or t.get("raw_description") or "N/A")[:45]
            print(f"           {t.get('date','')} | {t.get('type','')} | "
                  f"{t.get('amount','')} | {t.get('category','')} | {desc}")
else:
    report("step9", "GET /transactions/", False, f"status={r.status_code}")


# ============================================================
# FINAL SUMMARY
# ============================================================
print("")
print("=" * 60)
print("FINAL SUMMARY")
print("=" * 60)
total_tests = len(results)
passed = sum(1 for r in results.values() if r["passed"])
failed = sum(1 for r in results.values() if not r["passed"])

print(f"  Total tests:  {total_tests}")
print(f"  Passed:       {passed}")
print(f"  Failed:       {failed}")
print("")

if failed > 0:
    print("  FAILED tests:")
    for k, v in results.items():
        if not v["passed"]:
            print(f"    - {v['name']}: {v['detail']}")

print("")
verdict = "ALL TESTS PASSED" if failed == 0 else f"{failed} TEST(S) FAILED"
print(f"  VERDICT: {verdict}")
print("=" * 60)
