"""Minimal structured JSON logging with correlation-aware records."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from netops_api.core.config import Settings
from netops_api.core.request_context import get_correlation_id


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object without serializing request content."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "request_id", None) or get_correlation_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    """Configure a process-wide JSON handler once at application construction."""
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root_logger.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
