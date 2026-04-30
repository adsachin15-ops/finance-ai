# ⬡ Finance-AI

**Privacy-first · Local-first · AI-powered personal finance system.**

> Runs entirely on your machine. No cloud required. No data leaves your PC.

---

## Architecture

```
finance-ai/
├── backend/
│   ├── main.py                    # FastAPI app factory + lifespan
│   ├── core/
│   │   ├── config.py              # Pydantic Settings (env-validated)
│   │   ├── database.py            # SQLCipher engine + session factory
│   │   ├── logger.py              # structlog JSON logging
│   │   └── security.py            # PIN hashing, tokens, file validation
│   ├── models/                    # SQLAlchemy ORM models (8 tables)
│   ├── api/routes/                # FastAPI routers
│   │   ├── auth.py                # Register / Login / Guest / PIN change
│   │   ├── accounts.py            # CRUD for financial accounts
│   │   ├── transactions.py        # Query / filter / search / edit
│   │   ├── upload.py              # File ingestion pipeline
│   │   ├── dashboard.py           # Aggregated metrics + health score
│   │   └── insights.py            # AI insight retrieval
│   ├── services/file_parser/
│   │   ├── csv_parser.py          # Multi-format CSV (HDFC, SBI, ICICI, Axis...)
│   │   ├── excel_parser.py        # .xlsx → delegates to CSV pipeline
│   │   └── pdf_parser.py          # pdfplumber table extraction
│   └── ai/
│       └── categorizer.py         # Rule-based (Phase 1) + ML stub (Phase 2)
├── frontend/
│   ├── index.html                 # SPA shell
│   ├── assets/css/main.css        # Dark terminal aesthetic design system
│   └── assets/js/
│       ├── api.js                 # Fetch wrapper with auth injection
│       └── app.js                 # UI controller + Chart.js rendering
├── database/
│   └── schema.sql                 # Reference DDL + category seed data
└── scripts/
    └── start.sh                   # One-command setup + launch
```

---

## Quick Start

```bash
git clone <repo>
cd finance-ai
chmod +x scripts/start.sh
./scripts/start.sh
```

Open: **http://127.0.0.1:8000**

---

## Manual Setup

```bash
# Create venv
python3.11 -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows

# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env: set DB_ENCRYPTION_KEY and SECRET_KEY
# Generate: python -c "import secrets; print(secrets.token_hex(32))"

# Create dirs
mkdir -p database logs uploads/temp

# Run
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/auth/register` | POST | Create account |
| `/api/v1/auth/login` | POST | Login with phone + PIN |
| `/api/v1/auth/guest` | POST | Guest session |
| `/api/v1/auth/logout` | POST | Invalidate session |
| `/api/v1/accounts/` | GET/POST | List / create accounts |
| `/api/v1/transactions/` | GET | Paginated transaction list |
| `/api/v1/transactions/search` | GET | Full-text search |
| `/api/v1/upload/file` | POST | Upload CSV/Excel/PDF |
| `/api/v1/dashboard/summary` | GET | KPI metrics + health score |
| `/api/v1/dashboard/trend` | GET | Income vs expense trend |
| `/api/v1/dashboard/heatmap` | GET | Daily spending intensity |
| `/api/v1/insights/` | GET | AI-generated insights |
| `/health` | GET | System health check |
| `/api/docs` | GET | Swagger UI (dev only) |

---

## Security Design

| Control | Implementation |
|---------|---------------|
| Database encryption | SQLCipher AES-256-CBC |
| PIN storage | bcrypt rounds=12 |
| Session tokens | HMAC-SHA256 signed |
| File upload | Extension + MIME + size + path traversal check |
| CSV injection | Formula prefix neutralization |
| Account lockout | 5 failed attempts → 30min lockout |
| Guest data | In-memory SQLite, wiped on session end + atexit |
| CORS | Localhost only |
| Security headers | CSP, X-Frame-Options, X-Content-Type-Options |
| Deduplication | SHA-256 hash on (account, date, amount, description) |

---

## Supported CSV Formats

Tested column detection for:
- **HDFC**: Date, Narration, Debit Amount, Credit Amount, Closing Balance
- **SBI**: Txn Date, Description, Debit, Credit, Balance
- **ICICI**: Transaction Date, Transaction Remarks, Withdrawal Amount, Deposit Amount
- **Axis**: Tran Date, PARTICULARS, DEBIT, CREDIT, BALANCE
- **Kotak**: Date, Description, Withdrawal Amt., Deposit Amt., Bal

Any CSV with recognizable Date, Amount/Debit/Credit, and Description columns will parse.

---

## Roadmap

| Phase | Status | Features |
|-------|--------|---------|
| 1 — MVP | ✅ Built | CSV upload, categorization, dashboard, auth, guest mode |
| 2 — Core | 🔜 Next | Excel/PDF polish, multi-account analytics, ML categorizer |
| 3 — AI | 🔜 Future | Anomaly detection, spending forecast, financial health alerts |
| 4 — Cloud | 🔜 Future | Optional cloud backup, OTP auth, multi-device sync |
| 5 — Hybrid | 🔜 Future | Local + cloud, offline-first sync |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Database | SQLite + SQLCipher (AES-256) |
| ORM | SQLAlchemy 2.0 + Alembic |
| AI Phase 1 | Rule-based keyword categorizer |
| AI Phase 2 | scikit-learn (TF-IDF + LogisticRegression) |
| File parsing | pandas, pdfplumber, openpyxl |
| Security | bcrypt, python-jose, cryptography |
| Frontend | Vanilla HTML/CSS/JS + Chart.js |
| Logging | structlog (JSON) |
