import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import time
from typing import Any

import structlog

from app.core.config import settings


class UTCLogFormatter(logging.Formatter):
    """Render all stdlib and structlog records in one human-readable format."""

    converter = time.gmtime


def _render_log_message(logger: Any, method_name: str, event_dict: dict[str, Any]) -> str:
    event = str(event_dict.pop("event", "")).strip()
    if "level" in event_dict:
        event_dict.pop("level", None)
    if "timestamp" in event_dict:
        event_dict.pop("timestamp", None)
    details = " ".join(f"{key}={value}" for key, value in event_dict.items() if value is not None)
    if event and details:
        return f"{event} {details}"
    return event or details


def configure_logging() -> None:
    """Configure application logs for console/journal and optional rotating files."""
    log_level = getattr(logging, settings.LOG_LEVEL.strip().upper(), logging.INFO)
    formatter = UTCLogFormatter(
        fmt="%(asctime)sZ - %(levelname)s - [%(name)s] - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _render_log_message,
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
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger."""
    return structlog.get_logger(name)
