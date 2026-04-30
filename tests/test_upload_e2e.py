"""End-to-end upload test: PDF, Excel, duplicate detection, transaction verification."""
import httpx
import os

base = 'http://127.0.0.1:8000/api/v1'
project = r'c:\Users\sachi\OneDrive\Desktop\JS\finance-ai'

# ── Step 1: Guest login ──────────────────────────────────────────
print('=== Guest Login ===')
r = httpx.post(f'{base}/auth/guest', json={})
assert r.status_code == 200, f'Guest login failed: {r.status_code}'
token = r.json()['access_token']
headers = {'Authorization': f'Bearer {token}'}
print(f'  [PASS] Guest session created')

# ── Step 2: Create account ───────────────────────────────────────
print('')
print('=== Create Account ===')
r = httpx.post(f'{base}/accounts/', headers=headers, json={
    'nickname': 'ICICI Credit',
    'account_type': 'credit_card',
    'bank_name': 'ICICI',
    'current_balance': 0
})
assert r.status_code == 201, f'Account creation failed: {r.status_code} {r.text}'
icici_account_id = r.json()['id']
print(f'  [PASS] ICICI account created (id={icici_account_id})')

r = httpx.post(f'{base}/accounts/', headers=headers, json={
    'nickname': 'SBI Savings',
    'account_type': 'savings',
    'bank_name': 'SBI',
    'current_balance': 0
})
assert r.status_code == 201, f'Account creation failed: {r.status_code} {r.text}'
sbi_account_id = r.json()['id']
print(f'  [PASS] SBI account created (id={sbi_account_id})')

# ── Step 3: Upload ICICI credit PDF ──────────────────────────────
print('')
print('=== Upload: icici_credit.pdf ===')
pdf_path = os.path.join(project, 'icici_credit.pdf')
with open(pdf_path, 'rb') as f:
    r = httpx.post(
        f'{base}/upload/file',
        headers=headers,
        files={'file': ('icici_credit.pdf', f, 'application/pdf')},
        data={'account_id': str(icici_account_id)},
        timeout=60
    )
print(f'  Status: {r.status_code}')
if r.status_code in (200, 201):
    data = r.json()
    print(f'  [PASS] PDF upload succeeded')
    for k, v in data.items():
        print(f'    {k}: {v}')
    pdf_parsed = data.get('parsed_count', data.get('transactions_parsed', data.get('parsed', 0)))
else:
    print(f'  [FAIL] PDF upload failed: {r.text[:500]}')
    pdf_parsed = 0

# ── Step 4: Upload SBI Excel ─────────────────────────────────────
print('')
print('=== Upload: sbiex_unlocked.xlsx ===')
xlsx_path = os.path.join(project, 'sbiex_unlocked.xlsx')
with open(xlsx_path, 'rb') as f:
    r = httpx.post(
        f'{base}/upload/file',
        headers=headers,
        files={'file': ('sbiex_unlocked.xlsx', f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
        data={'account_id': str(sbi_account_id)},
        timeout=60
    )
print(f'  Status: {r.status_code}')
if r.status_code in (200, 201):
    data = r.json()
    print(f'  [PASS] Excel upload succeeded')
    for k, v in data.items():
        print(f'    {k}: {v}')
    xlsx_parsed = data.get('parsed_count', data.get('transactions_parsed', data.get('parsed', 0)))
else:
    print(f'  [FAIL] Excel upload failed: {r.text[:500]}')
    xlsx_parsed = 0

# ── Step 5: Upload same Excel again (duplicate detection) ────────
print('')
print('=== Duplicate Detection: sbiex_unlocked.xlsx (re-upload) ===')
with open(xlsx_path, 'rb') as f:
    r = httpx.post(
        f'{base}/upload/file',
        headers=headers,
        files={'file': ('sbiex_unlocked.xlsx', f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
        data={'account_id': str(sbi_account_id)},
        timeout=60
    )
print(f'  Status: {r.status_code}')
if r.status_code in (200, 201):
    data = r.json()
    dupes = data.get('duplicates', data.get('duplicates_skipped', data.get('duplicate_count', 'N/A')))
    print(f'  [PASS] Re-upload handled')
    for k, v in data.items():
        print(f'    {k}: {v}')
    if dupes and dupes != 'N/A' and int(dupes) > 0:
        print(f'  [PASS] Duplicate detection working: {dupes} duplicates detected')
    else:
        print(f'  [INFO] Duplicate count: {dupes}')
else:
    print(f'  [FAIL] Re-upload failed: {r.text[:500]}')

# ── Step 6: Verify transactions ──────────────────────────────────
print('')
print('=== Verify Transactions ===')
r = httpx.get(f'{base}/transactions/', headers=headers)
if r.status_code == 200:
    data = r.json()
    # Handle paginated vs list response
    if isinstance(data, list):
        txns = data
        total = len(data)
    elif isinstance(data, dict):
        txns = data.get('transactions', data.get('items', []))
        total = data.get('total', data.get('count', len(txns)))
    else:
        txns = []
        total = 0
    print(f'  [PASS] GET /transactions/ -> 200 (total={total}, page_items={len(txns)})')
    if txns:
        print(f'  Sample transactions:')
        for t in txns[:5]:
            desc = t.get('description', t.get('raw_description', 'N/A'))
            print(f'    {t.get("date","")} | {t.get("type","")} | {t.get("amount","")} | {desc[:50]}')
else:
    print(f'  [FAIL] GET /transactions/ -> {r.status_code}')

# ── Step 7: Check dashboard summary ──────────────────────────────
print('')
print('=== Dashboard Summary ===')
r = httpx.get(f'{base}/dashboard/summary?period=monthly', headers=headers)
if r.status_code == 200:
    data = r.json()
    print(f'  [PASS] Dashboard loaded')
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            print(f'    {k}: {type(v).__name__} ({len(v)} items)')
        else:
            print(f'    {k}: {v}')
else:
    print(f'  [FAIL] Dashboard -> {r.status_code}: {r.text[:200]}')

# ── Step 8: Check upload logs ────────────────────────────────────
print('')
print('=== Upload Logs ===')
r = httpx.get(f'{base}/upload/logs', headers=headers)
if r.status_code == 200:
    logs = r.json()
    if isinstance(logs, list):
        print(f'  [PASS] Upload logs: {len(logs)} entries')
        for log in logs:
            print(f'    {log.get("filename","")} | status={log.get("status","")} | parsed={log.get("parsed_count","")}')
    else:
        print(f'  [PASS] Upload logs: {logs}')
else:
    print(f'  [FAIL] Upload logs -> {r.status_code}')

print('')
print('=== E2E TEST COMPLETE ===')
