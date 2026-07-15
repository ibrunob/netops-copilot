"""HTTP middleware for request-scoped observability and abuse controls."""

from __future__ import annotations

import logging
import uuid
from time import perf_counter
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from netops_api.core.config import Settings
from netops_api.core.rate_limit import FixedWindowRateLimiter
from netops_api.core.request_context import (
    new_trace_context,
    reset_correlation_id,
    reset_trace_context,
    set_correlation_id,
    set_trace_context,
)

CORRELATION_ID_HEADER = "X-Correlation-ID"
TRACEPARENT_HEADER = "traceparent"
HEALTH_PATHS = frozenset({"/healthz", "/readyz"})
logger = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accept a valid correlation ID and continue/create a W3C trace."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = _valid_or_new_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
        trace_context = new_trace_context(request.headers.get(TRACEPARENT_HEADER))
        request.state.correlation_id = correlation_id
        correlation_token = set_correlation_id(correlation_id)
        trace_token = set_trace_context(trace_context)
        try:
            response = await call_next(request)
        finally:
            reset_trace_context(trace_token)
            reset_correlation_id(correlation_token)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[TRACEPARENT_HEADER] = trace_context.traceparent
        return response


class RequestTelemetryMiddleware(BaseHTTPMiddleware):
    """Log bounded request metadata after routing has selected a route template."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started_at = perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        logger.info(
            "http_request_completed",
            extra={
                "http": {
                    "method": request.method,
                    "route": route_path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1_000, 3),
                }
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a local per-client baseline limit without trusting forwarded headers."""

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self._enabled = settings.rate_limit_enabled
        self._limiter = FixedWindowRateLimiter(
            max_requests=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self._enabled or request.url.path in HEALTH_PATHS:
            return await call_next(request)
        client_host = request.client.host if request.client else "unknown"
        allowed, retry_after = self._limiter.check(client_host)
        if allowed:
            return await call_next(request)
        request_id = getattr(request.state, "correlation_id", "-")
        logger.warning(
            "rate_limit_exceeded",
            extra={"rate_limit": {"scope": "client", "retry_after_seconds": retry_after}},
        )
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please retry later.",
                    "request_id": request_id,
                    "details": None,
                }
            },
            headers={CORRELATION_ID_HEADER: request_id, "Retry-After": str(retry_after)},
        )


def _valid_or_new_correlation_id(value: str | None) -> str:
    if value:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            pass
    return str(uuid.uuid4())
