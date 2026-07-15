"""Minimal structured logging for worker runtime lifecycle events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from netops_worker.config import WorkerSettings


class JsonFormatter(logging.Formatter):
    """Emit lifecycle metadata without serializing Temporal payloads or task inputs."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "service": self._service_name,
        }
        exception_type = record.exc_info[0] if record.exc_info else None
        if exception_type is not None:
            payload["exception_type"] = exception_type.__name__
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(settings: WorkerSettings) -> None:
    """Configure process logging once, with no unbounded request or workflow data."""
    root_logger = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root_logger.handlers):
        return
    root_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(settings.service_name))
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
