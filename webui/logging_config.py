"""Structured logging configuration with JSON formatter."""

import json
import logging
from datetime import UTC, datetime

_STANDARD_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)


class JSONFormatter(logging.Formatter):
    """Format log records as JSON with timestamp, level, logger, and message."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # logger.info(..., extra={"key": val}) で渡された追加コンテキストも含める
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                log_data[key] = value
        return json.dumps(log_data, default=str)


def configure_logging() -> None:
    """Configure structured JSON logging for the application."""
    formatter = JSONFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)
