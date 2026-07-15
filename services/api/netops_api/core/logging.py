"""Structured, correlation-aware logging that never emits raw operator content."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from netops_api.core.config import Settings
from netops_api.core.request_context import get_correlation_id, get_trace_id

REDACTED = "[REDACTED]"
MAX_VALUE_LENGTH = 2_048
MAX_COLLECTION_ITEMS = 50
MAX_NESTING_DEPTH = 5

_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?:authorization|cookie|credential|password|secret|token|api[_-]?key|"
    r"private[_-]?key|pre[_-]?shared|snmp[_-]?community|session|config|artifact|audio|body|content)",
    re.IGNORECASE,
)
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization|password|passwd|secret|token|api[_-]?key|"
    r"snmp-server\s+community|crypto\s+isakmp\s+key)\s*(?:=|:|\s+)\s*[^\s,;]+"
)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._~+/-]+=*")
_BASE_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "request_id",
    "trace_id",
}


def redact_for_observability(value: Any, *, field_name: str | None = None, depth: int = 0) -> Any:
    """Return bounded, recursively redacted data suitable for logs or error reports.

    Observability must carry identifiers and timings, not configurations, uploads,
    HTTP bodies, credentials, or other operator-supplied evidence. This defensive
    helper complements the rule that callers pass event names and explicit metadata.
    """
    if field_name and _SENSITIVE_FIELD_PATTERN.search(field_name):
        return REDACTED
    if depth >= MAX_NESTING_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        items = list(value.items())[:MAX_COLLECTION_ITEMS]
        result: dict[str, Any] = {
            str(key): redact_for_observability(item, field_name=str(key), depth=depth + 1)
            for key, item in items
        }
        if len(value) > MAX_COLLECTION_ITEMS:
            result["_truncated"] = True
        return result
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)[:MAX_COLLECTION_ITEMS]
        sequence_result = [redact_for_observability(item, depth=depth + 1) for item in items]
        if len(value) > MAX_COLLECTION_ITEMS:
            sequence_result.append("[TRUNCATED]")
        return sequence_result
    return _redact_text(str(value))


def _redact_text(value: str) -> str:
    redacted = _BEARER_TOKEN_PATTERN.sub("Bearer " + REDACTED, value)
    redacted = _INLINE_SECRET_PATTERN.sub(REDACTED, redacted)
    if len(redacted) > MAX_VALUE_LENGTH:
        return redacted[:MAX_VALUE_LENGTH] + "[TRUNCATED]"
    return redacted


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object without serializing request content."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": _redact_text(record.getMessage()),
            "service": self._service_name,
            "correlation_id": getattr(record, "request_id", None) or get_correlation_id(),
        }
        trace_id = getattr(record, "trace_id", None) or get_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        exception_type = record.exc_info[0] if record.exc_info else None
        if exception_type is not None:
            # Exception messages can contain rejected configuration. The error type is
            # operationally useful without leaking the value that caused the failure.
            payload["exception_type"] = exception_type.__name__
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _BASE_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["attributes"] = redact_for_observability(extras)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    """Configure a process-wide JSON handler once at application construction."""
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root_logger.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(settings.service_name))
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
