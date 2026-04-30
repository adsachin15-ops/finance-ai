"""Test script for guest authentication refactor."""
import httpx

base = 'http://127.0.0.1:8000/api/v1'

# Step 3: Guest auth
print('=== STEP 3: Guest Authentication ===')
r = httpx.post(f'{base}/auth/guest', json={})
print(f'POST /auth/guest -> {r.status_code}')
data = r.json()
print(f'  is_guest: {data.get("is_guest")}')
print(f'  display_name: {data.get("display_name")}')
token = data.get('access_token', '')
print(f'  token: {token[:20]}...' if token else '  token: MISSING')

if r.status_code != 200 or not token:
    print('FAILED: Could not get guest token')
    exit(1)

headers = {'Authorization': f'Bearer {token}'}

# Step 4: Test all endpoints
print('')
print('=== STEP 4: Guest API Access ===')
endpoints = [
    ('GET', '/accounts/'),
    ('GET', '/dashboard/summary?period=monthly'),
    ('GET', '/dashboard/trend?granularity=monthly&months=6'),
    ('GET', '/transactions/'),
    ('GET', '/insights/'),
    ('GET', '/upload/logs'),
]

all_pass = True
for method, path in endpoints:
    r = httpx.request(method, f'{base}{path}', headers=headers)
    ok = r.status_code == 200
    icon = 'PASS' if ok else 'FAIL'
    if not ok:
        all_pass = False
        try:
            detail = r.json().get('detail', '')
        except Exception:
            detail = ''
        print(f'  [{icon}] {method} {path} -> {r.status_code} ({detail})')
    else:
        print(f'  [{icon}] {method} {path} -> {r.status_code}')

# Test account creation in guest mode
print('')
print('=== Account Creation (Guest) ===')
r = httpx.post(f'{base}/accounts/', headers=headers, json={
    'nickname': 'Test SBI',
    'account_type': 'savings',
    'bank_name': 'SBI',
    'current_balance': 0
})
ok = r.status_code == 201
icon = 'PASS' if ok else 'FAIL'
if not ok:
    all_pass = False
    try:
        detail = r.json().get('detail', '')
    except Exception:
        detail = ''
    print(f'  [{icon}] POST /accounts/ -> {r.status_code} ({detail})')
else:
    acct = r.json()
    print(f'  [{icon}] POST /accounts/ -> {r.status_code} (id={acct["id"]}, nick={acct["nickname"]})')

# Verify account shows in list
print('')
print('=== Verify Account List ===')
r = httpx.get(f'{base}/accounts/', headers=headers)
ok = r.status_code == 200
icon = 'PASS' if ok else 'FAIL'
if ok:
    accounts = r.json()
    print(f'  [{icon}] GET /accounts/ -> {r.status_code} (count={len(accounts)})')
    for a in accounts:
        print(f'    - {a["nickname"]} ({a["account_type"]}, balance={a["current_balance"]})')
else:
    all_pass = False
    print(f'  [{icon}] GET /accounts/ -> {r.status_code}')

print('')
result = 'ALL PASSED' if all_pass else 'SOME FAILED'
print(f'=== OVERALL: {result} ===')
