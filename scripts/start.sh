#!/usr/bin/env bash
# ============================================================
# Finance-AI — Start Script
# Usage: bash scripts/start.sh
# ============================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "  ⬡  Finance-AI"
echo "  ─────────────────────────────"

# Check venv exists
if [ ! -f "venv/bin/activate" ]; then
  echo "  ✗ venv not found. Run: python3 -m venv venv && pip install -r requirements.txt"
  exit 1
fi

source venv/bin/activate

# Check .env exists
if [ ! -f ".env" ]; then
  echo "  ✗ .env not found. Copy .env.example to .env and fill in values."
  exit 1
fi

# Create required directories
mkdir -p database logs uploads/temp backup

echo "  ✓ Starting on http://127.0.0.1:8000"
echo "  ✓ API docs:  http://127.0.0.1:8000/api/docs"
echo "  Press Ctrl+C to stop."
echo ""

python -m uvicorn backend.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload \
  --log-level warning
