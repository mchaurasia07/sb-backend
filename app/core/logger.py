import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Any

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Configure structured JSON logs suitable for production aggregation."""
    log_level = getattr(logging, settings.LOG_LEVEL.strip().upper(), logging.INFO)
    formatter = logging.Formatter("%(message)s")
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        timestamper,
        structlog.processors.JSONRenderer(),
    ]

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    handlers: list[logging.Handler] = [console_handler]
    if settings.LOG_TO_FILE:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / settings.LOG_FILE_NAME,
            maxBytes=max(1, settings.LOG_FILE_MAX_BYTES),
            backupCount=max(0, settings.LOG_FILE_BACKUP_COUNT),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    logging.basicConfig(level=log_level, handlers=handlers, force=True)
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
