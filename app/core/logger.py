import logging
import sys
from typing import Any

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Configure structured JSON logs suitable for production aggregation."""
    log_level = getattr(logging, settings.LOG_LEVEL.strip().upper(), logging.INFO)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        timestamper,
        structlog.processors.JSONRenderer(),
    ]

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level, force=True)
    for logger_name in (
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
        "aiomysql",
        "asyncmy",
        "google.genai.models",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger."""
    return structlog.get_logger(name)
