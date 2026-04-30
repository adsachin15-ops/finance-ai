"""
backend/main.py
─────────────────────────────────────────────────────────────
Finance-AI — FastAPI Application Entry Point
"""

from __future__ import annotations

import atexit
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.middleware.rate_limiter import RateLimitMiddleware
from backend.core.config import get_settings
from backend.core.database import check_db_health, init_db, wipe_guest_db
from backend.core.logger import (
    bind_request_context,
    configure_logging,
    get_logger,
)

settings = get_settings()

configure_logging(
    level=settings.log_level,
    log_file=settings.log_file,
    log_format=settings.log_format,
)

log = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "app.startup",
        version=settings.app_version,
        env=settings.app_env,
    )

    settings.ensure_dirs()

    import backend.models  # noqa

    init_db()
    _seed_categories()

    atexit.register(wipe_guest_db)

    log.info(
        "app.startup.complete",
        host=settings.host,
        port=settings.port,
    )

    yield

    log.info("app.shutdown.initiated")
    wipe_guest_db()
    log.info("app.shutdown.complete")


# ── Application Factory ───────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Privacy-first, AI-powered personal finance system. "
            "Runs locally. No cloud required."
        ),
        docs_url="/api/docs" if settings.is_development else None,
        redoc_url="/api/redoc" if settings.is_development else None,
        openapi_url=(
            "/api/openapi.json"
            if settings.is_development
            else None
        ),
        lifespan=lifespan,
    )

    _register_middleware(app)
    _register_routers(app)
    _register_exception_handlers(app)
    _register_static_files(app)

    return app


# ── Middleware ────────────────────────────────────────────────────

def _register_middleware(app: FastAPI) -> None:

    # ── Environment-aware CORS configuration ──
    # In production, set CORS_ORIGINS env var to your domain
    # e.g. CORS_ORIGINS=https://finance.yourdomain.com

    import os
    _extra_origins = os.getenv("CORS_ORIGINS", "")

    _origins = [
        f"http://localhost:{settings.port}",
        f"http://127.0.0.1:{settings.port}",
    ]

    # Add production origins from env var
    if _extra_origins:
        _origins.extend(
            o.strip() for o in _extra_origins.split(",") if o.strip()
        )

    # Allow frontend dev server only in development
    if settings.is_development:
        _origins.append("http://localhost:3000")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "OPTIONS",
        ],
        allow_headers=["*"],
    )

    # Rate limiting
    app.add_middleware(RateLimitMiddleware)

    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next,
    ):
        request_id = str(uuid.uuid4())[:8]
        start_time = time.perf_counter()

        bind_request_context(
            request_id=request_id,
            session_id=request.headers.get(
                "X-Session-ID"
            ),
        )

        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            client=(
                request.client.host
                if request.client
                else "unknown"
            ),
        )

        response: Response = await call_next(
            request
        )

        duration_ms = round(
            (time.perf_counter() - start_time)
            * 1000,
            2,
        )

        log.info(
            "http.response",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers[
            "X-Request-ID"
        ] = request_id

        return response

    @app.middleware("http")
    async def security_headers_middleware(
        request: Request,
        call_next,
    ):
        response = await call_next(request)

        response.headers[
            "X-Content-Type-Options"
        ] = "nosniff"

        response.headers[
            "X-Frame-Options"
        ] = "DENY"

        response.headers[
            "X-XSS-Protection"
        ] = "1; mode=block"

        response.headers[
            "Referrer-Policy"
        ] = (
            "strict-origin-when-cross-origin"
        )

        response.headers[
            "Cache-Control"
        ] = "no-store"

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:;"
        )

        # NEW — I-5 Security hardening
        response.headers["Permissions-Policy"] = (
            "camera=(), "
            "microphone=(), "
            "geolocation=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=()"
        )

        return response


# ── Routers ───────────────────────────────────────────────────────

def _register_routers(app: FastAPI) -> None:
    from backend.api.routes import (
        accounts,
        auth,
        dashboard,
        insights,
        reports,
        transactions,
        upload,
    )

    PREFIX = "/api/v1"

    app.include_router(
        auth.router,
        prefix=f"{PREFIX}/auth",
        tags=["Authentication"],
    )

    app.include_router(
        accounts.router,
        prefix=f"{PREFIX}/accounts",
        tags=["Accounts"],
    )

    app.include_router(
        transactions.router,
        prefix=f"{PREFIX}/transactions",
        tags=["Transactions"],
    )

    app.include_router(
        upload.router,
        prefix=f"{PREFIX}/upload",
        tags=["Upload"],
    )

    app.include_router(
        dashboard.router,
        prefix=f"{PREFIX}/dashboard",
        tags=["Dashboard"],
    )

    app.include_router(
        insights.router,
        prefix=f"{PREFIX}/insights",
        tags=["Insights"],
    )

    app.include_router(
        reports.router,
        prefix=f"{PREFIX}/reports",
        tags=["Reports"],
    )

    @app.get("/health", tags=["System"])
    async def health_check() -> dict[str, Any]:

        db_status = check_db_health()
        all_healthy = all(db_status.values())

        return {
            "status": (
                "healthy"
                if all_healthy
                else "degraded"
            ),
            "version": settings.app_version,
            "environment": settings.app_env,
            "database": db_status,
            "cloud_enabled": settings.cloud_enabled,
        }

    @app.get("/api/v1/system/info", tags=["System"])
    async def system_info() -> dict[str, Any]:
        """
        System metadata endpoint.
        Used by integration tests and monitoring.
        """

        return {
            "app_name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
            "features": {
                "cloud": settings.cloud_enabled,
                "ml_categorization": bool(settings.model_path),
                "pdf_parsing": True,
                "multi_user": True,
            },
        }

# ── Exception Handlers ────────────────────────────────────────────

def _register_exception_handlers(
    app: FastAPI,
) -> None:

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request,
        exc: ValueError,
    ):
        log.warning(
            "error.validation",
            path=request.url.path,
            error=str(exc),
        )

        return JSONResponse(
            status_code=422,
            content={
                "error": "Validation Error",
                "detail": str(exc),
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(
        request: Request,
        exc: Exception,
    ):
        log.error(
            "error.unhandled",
            path=request.url.path,
            error=str(exc),
            exc_info=True,
        )

        detail = (
            str(exc)
            if settings.is_development
            else "Internal server error."
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal Error",
                "detail": detail,
            },
        )


# ── Static Files ──────────────────────────────────────────────────

def _register_static_files(
    app: FastAPI,
) -> None:

    frontend_path = (
        Path(__file__).parent.parent
        / "frontend"
    )

    if frontend_path.exists():
        app.mount(
            "/",
            StaticFiles(
                directory=str(frontend_path),
                html=True,
            ),
            name="frontend",
        )
    else:
        log.warning(
            "static.files.missing",
            path=str(frontend_path),
        )


# ── Category Seeding ──────────────────────────────────────────────

def _seed_categories() -> None:
    from backend.core.database import db_session
    from backend.models.category import Category

    DEFAULTS = [
        ("Food", None, "#FF6B35"),
        ("Travel", None, "#4ECDC4"),
        ("Shopping", None, "#A29BFE"),
        ("Bills", None, "#55EFC4"),
        ("Health", None, "#FD79A8"),
        ("Entertainment", None, "#FFEAA7"),
        ("Finance", None, "#636E72"),
        ("Education", None, "#74B9FF"),
        ("Transfer", None, "#B2BEC3"),
        ("Income", None, "#00B894"),
        ("Other", None, "#DFE6E9"),
    ]

    try:
        with db_session() as db:

            count = db.query(Category).count()

            if count == 0:

                for name, parent, color in DEFAULTS:

                    db.add(
                        Category(
                            name=name,
                            parent_category=parent,
                            color=color,
                            is_system=True,
                        )
                    )

                log.info(
                    "db.seed.categories",
                    count=len(DEFAULTS),
                )

    except Exception as e:

        log.warning(
            "db.seed.categories.failed",
            error=str(e),
        )


# ── App Instance ──────────────────────────────────────────────────

app = create_app()


# ── Dev Runner ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
        access_log=False,
    )