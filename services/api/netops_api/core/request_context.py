"""Context-local values used by logging, tracing, and error reporting."""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar, Token
from dataclasses import dataclass

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
_trace_context: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)

_TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<parent_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """A W3C trace context safe to place in logs and response headers."""

    trace_id: str
    span_id: str
    trace_flags: str

    @property
    def traceparent(self) -> str:
        """Render the current server span as a W3C ``traceparent`` value."""
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"


def get_correlation_id() -> str:
    """Return the current request correlation ID, if a request is active."""
    return _correlation_id.get()


def set_correlation_id(correlation_id: str) -> Token[str]:
    """Set a correlation ID and return the token necessary to restore context."""
    return _correlation_id.set(correlation_id)


def reset_correlation_id(token: Token[str]) -> None:
    """Restore the previous correlation context after a request completes."""
    _correlation_id.reset(token)


def get_trace_context() -> TraceContext | None:
    """Return the current W3C trace context, if a request is active."""
    return _trace_context.get()


def get_trace_id() -> str | None:
    """Return only the current trace ID for structured log records."""
    context = get_trace_context()
    return context.trace_id if context else None


def new_trace_context(traceparent: str | None) -> TraceContext:
    """Continue a valid incoming trace or create a sampled root trace.

    Malformed values are not reflected in headers or logs, preventing client-controlled
    high-cardinality and injection values from becoming telemetry data.
    """
    match = _TRACEPARENT_PATTERN.fullmatch(traceparent or "")
    if match and match["version"] != "ff":
        trace_id = match["trace_id"]
        parent_id = match["parent_id"]
        if trace_id != "0" * 32 and parent_id != "0" * 16:
            return TraceContext(
                trace_id=trace_id,
                span_id=secrets.token_hex(8),
                trace_flags=match["flags"],
            )
    return TraceContext(
        trace_id=secrets.token_hex(16),
        span_id=secrets.token_hex(8),
        trace_flags="01",
    )


def set_trace_context(context: TraceContext) -> Token[TraceContext | None]:
    """Set request trace context and return its restoration token."""
    return _trace_context.set(context)


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    """Restore the prior trace context after the request completes."""
    _trace_context.reset(token)
