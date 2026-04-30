"""
backend/core/logger.py
─────────────────────────────────────────────────────────────
Structured logging via structlog.

Usage:
    from backend.core.logger import get_logger
    log = get_logger(__name__)
    log.info("upload.complete", file="bank.csv", records=312)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import structlog


def configure_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    log_format: str = "console",
) -> None:
    """
    Configure structlog + stdlib logging.
    Call once at application startup in main.py lifespan.
    """

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handlers: list[logging.Handler] = []

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        from pathlib import Path

        log_path = Path(log_file)

# Ensure directory exists
        log_path.parent.mkdir(parents=True, exist_ok=True)

# If path accidentally points to directory, fix it
        if log_path.is_dir():
           log_path = log_path / "finance-ai.log"

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root_logger = logging.getLogger()
    root_logger.handlers = handlers
    root_logger.setLevel(level.upper())

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(
    request_id: str,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
    )
