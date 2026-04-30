#!/usr/bin/env bash
# ============================================================
# Finance-AI — Migration Helper
# Usage: bash scripts/migrate.sh [command]
# ============================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f "venv/bin/activate" ]; then
  echo "✗ venv not found."
  exit 1
fi

source venv/bin/activate

CMD=${1:-"status"}

case "$CMD" in

  status)
    echo "── Current revision ──"
    alembic current
    echo ""
    echo "── Migration history ──"
    alembic history --verbose
    ;;

  upgrade)
    echo "── Upgrading to head ──"
    alembic upgrade head
    echo "── Done ──"
    alembic current
    ;;

  downgrade)
    STEPS=${2:-"-1"}
    echo "── Downgrading $STEPS step(s) ──"
    alembic downgrade "$STEPS"
    echo "── Done ──"
    alembic current
    ;;

  new)
    MSG=${2:-"schema_change"}
    echo "── Creating new migration: $MSG ──"
    alembic revision --autogenerate -m "$MSG"
    echo "── Review the generated file in database/migrations/versions/ ──"
    ;;

  sql)
    echo "── Generating SQL for upgrade to head ──"
    alembic upgrade head --sql
    ;;

  *)
    echo "Usage: bash scripts/migrate.sh [status|upgrade|downgrade|new|sql]"
    echo ""
    echo "  status           show current revision and history"
    echo "  upgrade          apply all pending migrations"
    echo "  downgrade [N]    downgrade N steps (default: 1)"
    echo "  new [message]    autogenerate a new migration"
    echo "  sql              print SQL without applying"
    ;;

esac
