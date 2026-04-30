#!/usr/bin/env bash
# ============================================================
# Finance-AI — Docker Helper Script
# Usage: bash scripts/docker.sh [command]
# ============================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

CMD=${1:-"help"}

# ── Color output ──────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}  ✓ $1${NC}"; }
warning() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
error()   { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

# ── Check .env ────────────────────────────────────────────────────
check_env() {
    if [ ! -f ".env" ]; then
        warning ".env not found. Copying from .env.example..."
        cp .env.example .env
        warning "Edit .env and set DB_ENCRYPTION_KEY and SECRET_KEY before running."
        exit 1
    fi

    # Check required keys are not placeholder values
    if grep -q "CHANGE_ME" .env; then
        error ".env contains placeholder values. Set real keys before running."
    fi
    info ".env OK"
}

case "$CMD" in

  # ── Development ────────────────────────────────────────────────
  dev)
    echo ""
    echo "  ⬡  Finance-AI — Development"
    check_env
    info "Building and starting development container..."
    docker compose up --build
    ;;

  dev:detach)
    check_env
    info "Starting development container in background..."
    docker compose up --build -d
    info "Running at http://127.0.0.1:8000"
    ;;

  # ── Production ─────────────────────────────────────────────────
  prod)
    echo ""
    echo "  ⬡  Finance-AI — Production"
    check_env
    info "Building and starting production container..."
    docker compose -f docker-compose.prod.yml up --build -d
    info "Running at http://127.0.0.1:8000"
    ;;

  # ── Stop ───────────────────────────────────────────────────────
  stop)
    info "Stopping containers..."
    docker compose down 2>/dev/null || true
    docker compose -f docker-compose.prod.yml down 2>/dev/null || true
    info "Stopped."
    ;;

  # ── Logs ───────────────────────────────────────────────────────
  logs)
    docker compose logs -f --tail=100
    ;;

  logs:prod)
    docker compose -f docker-compose.prod.yml logs -f --tail=100
    ;;

  # ── Shell ──────────────────────────────────────────────────────
  shell)
    info "Opening shell in development container..."
    docker compose exec app bash || \
    docker compose exec app sh
    ;;

  # ── Build ──────────────────────────────────────────────────────
  build)
    info "Building development image..."
    docker build -t finance-ai:dev .
    info "Build complete."
    docker images finance-ai
    ;;

  build:prod)
    info "Building production image..."
    docker build -t finance-ai:prod --target runtime .
    info "Build complete."
    docker images finance-ai
    ;;

  # ── Database ───────────────────────────────────────────────────
  migrate)
    info "Running migrations in container..."
    docker compose exec app alembic upgrade head
    ;;

  db:shell)
    info "Opening database shell..."
    VOLUME=$(docker volume inspect finance-ai-data \
      --format '{{ .Mountpoint }}' 2>/dev/null)
    if [ -z "$VOLUME" ]; then
        error "Volume finance-ai-data not found. Start container first."
    fi
    sqlite3 "${VOLUME}/finance.db"
    ;;

  # ── Backup ─────────────────────────────────────────────────────
  backup)
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="$PROJECT_ROOT/backup"
    mkdir -p "$BACKUP_DIR"

    VOLUME=$(docker volume inspect finance-ai-data \
      --format '{{ .Mountpoint }}' 2>/dev/null)

    if [ -z "$VOLUME" ]; then
        warning "Docker volume not found. Backing up local database..."
        if [ -f "database/finance.db" ]; then
            cp database/finance.db "${BACKUP_DIR}/finance_${TIMESTAMP}.db"
            info "Backup saved: backup/finance_${TIMESTAMP}.db"
        else
            error "No database found to back up."
        fi
    else
        cp "${VOLUME}/finance.db" "${BACKUP_DIR}/finance_${TIMESTAMP}.db"
        info "Backup saved: backup/finance_${TIMESTAMP}.db"
    fi
    ;;

  # ── Status ─────────────────────────────────────────────────────
  status)
    echo ""
    echo "  ── Container Status ──"
    docker compose ps 2>/dev/null || echo "  No dev containers running"
    echo ""
    echo "  ── Images ──"
    docker images finance-ai 2>/dev/null || echo "  No images built"
    echo ""
    echo "  ── Volumes ──"
    docker volume ls --filter name=finance-ai 2>/dev/null
    ;;

  # ── Cleanup ────────────────────────────────────────────────────
  clean)
    warning "This will remove all Finance-AI containers and images."
    read -p "  Continue? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        docker compose down --rmi local 2>/dev/null || true
        docker compose -f docker-compose.prod.yml down --rmi local 2>/dev/null || true
        info "Cleanup complete."
    else
        info "Cancelled."
    fi
    ;;

  clean:volumes)
    warning "This will DELETE ALL DATA in Finance-AI volumes."
    read -p "  Are you sure? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        docker compose down -v 2>/dev/null || true
        docker volume rm finance-ai-data finance-ai-logs 2>/dev/null || true
        info "Volumes removed."
    else
        info "Cancelled."
    fi
    ;;

  # ── Help ───────────────────────────────────────────────────────
  help|*)
    echo ""
    echo "  ⬡  Finance-AI Docker Helper"
    echo "  ────────────────────────────────────────"
    echo ""
    echo "  Development:"
    echo "    bash scripts/docker.sh dev           start with hot reload"
    echo "    bash scripts/docker.sh dev:detach    start in background"
    echo "    bash scripts/docker.sh logs          follow logs"
    echo "    bash scripts/docker.sh shell         open container shell"
    echo ""
    echo "  Production:"
    echo "    bash scripts/docker.sh prod          start production"
    echo "    bash scripts/docker.sh logs:prod     follow prod logs"
    echo "    bash scripts/docker.sh build:prod    build prod image"
    echo ""
    echo "  Database:"
    echo "    bash scripts/docker.sh migrate       run alembic upgrade head"
    echo "    bash scripts/docker.sh backup        backup database file"
    echo "    bash scripts/docker.sh db:shell      open sqlite3 shell"
    echo ""
    echo "  Management:"
    echo "    bash scripts/docker.sh status        show containers/images"
    echo "    bash scripts/docker.sh stop          stop all containers"
    echo "    bash scripts/docker.sh clean         remove containers/images"
    echo "    bash scripts/docker.sh clean:volumes WARNING: deletes all data"
    echo ""
    ;;

esac
