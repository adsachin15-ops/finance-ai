# ============================================================
# Finance-AI — Dockerfile
# Multi-stage build for minimal production image
# ============================================================
#
# Stages:
#   builder  → install Python dependencies
#   runtime  → minimal runtime image
#
# Why multi-stage?
#   builder stage needs gcc, build tools (~500MB).
#   runtime stage copies only installed packages (~150MB).
#   Final image is 3x smaller than a single-stage build.
#
# Build:
#   docker build -t finance-ai:latest .
#
# Run:
#   docker run -p 8000:8000 --env-file .env finance-ai:latest
# ============================================================

# ── Stage 1: Builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Set build environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install system build dependencies
# These are needed to compile certain Python packages (bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (Docker layer cache optimization)
# If requirements.txt does not change, this layer is cached
COPY requirements.txt .

# Install Python dependencies into /build/wheels
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Metadata
LABEL maintainer="Finance-AI" \
      version="1.0.0" \
      description="Privacy-first AI-powered personal finance system"

# Runtime environment
# Secrets (DB_ENCRYPTION_KEY, SECRET_KEY) must be injected at runtime
# via --env-file or docker-compose environment. Never hardcode them here.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/app/database/finance.db

# Install runtime system dependencies only
# libmagic1: MIME type detection for file uploads
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create non-root user for security
# Running as root inside container is a security risk
RUN groupadd -r financeai && \
    useradd -r -g financeai -d /app -s /sbin/nologin financeai

# Set working directory
WORKDIR /app

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY database/migrations/ ./database/migrations/
COPY alembic.ini .
COPY .env.example .

# Create required runtime directories
# These are either mounted as volumes or created fresh
# /app/data is used in production (via volume mount)
# /app/database is used in development (local filesystem)
RUN mkdir -p \
    database \
    data \
    logs \
    uploads/temp \
    backup && \
    chown -R financeai:financeai /app

# Switch to non-root user
USER financeai

# Expose application port
EXPOSE 8000

# Health check
# Docker/Render will mark container as unhealthy if /health returns non-200
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Startup command
# workers=1 required for SQLite (single-file DB, no concurrent writes)
# Uses shell form so $PORT is resolved at runtime (required for Render)
CMD python -m uvicorn backend.main:app \
     --host 0.0.0.0 \
     --port ${PORT:-8000} \
     --workers 1 \
     --log-level warning \
     --no-access-log
