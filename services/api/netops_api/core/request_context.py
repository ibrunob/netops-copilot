"""Context-local values used by logging and error reporting."""

from __future__ import annotations

from contextvars import ContextVar, Token

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    """Return the current request correlation ID, if a request is active."""
    return _correlation_id.get()


def set_correlation_id(correlation_id: str) -> Token[str]:
    """Set a correlation ID and return the token necessary to restore context."""
    return _correlation_id.set(correlation_id)


def reset_correlation_id(token: Token[str]) -> None:
    """Restore the previous correlation context after a request completes."""
    _correlation_id.reset(token)
