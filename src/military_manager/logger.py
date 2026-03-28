"""Structured JSON logging with correlation IDs."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pythonjsonlogger import jsonlogger

from military_manager.config import LOG_LEVEL, APP_NAME, DATA_DIR


def _generate_correlation_id() -> str:
    """Generate a correlation ID: YYYYMMDD-<6 random chars>."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_id = uuid.uuid4().hex[:6]
    return f"{date_str}-{short_id}"


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom formatter matching the spec's JSON log format."""

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["app"] = APP_NAME
        if not log_record.get("correlation_id"):
            log_record["correlation_id"] = _generate_correlation_id()


def setup_logging() -> logging.Logger:
    """Configure and return the application logger."""
    logger = logging.getLogger(APP_NAME)

    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # Console handler (human-readable)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # File handler (structured JSON) — only on local/SQLite; Streamlit Cloud uses stdout
    from military_manager.config import IS_POSTGRES
    if not IS_POSTGRES:
        log_file = DATA_DIR / "app.log"
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        json_formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s"
        )
        file_handler.setFormatter(json_formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """Get the application logger."""
    return logging.getLogger(APP_NAME)


def log_action(action: str, details: dict | None = None, level: str = "INFO",
               correlation_id: str | None = None) -> None:
    """Log a structured action with optional details."""
    logger = get_logger()
    extra = {
        "action": action,
        "correlation_id": correlation_id or _generate_correlation_id(),
    }
    if details:
        extra["details"] = details
    log_func = getattr(logger, level.lower(), logger.info)
    log_func(action, extra=extra)
