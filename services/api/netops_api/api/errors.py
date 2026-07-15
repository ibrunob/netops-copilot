"""Typed, content-safe error responses shared by all HTTP endpoints."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from netops_api.core.request_context import get_correlation_id

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    """Stable error envelope returned by every API failure."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(examples=["validation_error"])
    message: str = Field(examples=["The submitted request is invalid."])
    request_id: str = Field(examples=["0d8049a8-ea40-4407-b1f4-690b28ee5d46"])
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    """Top-level error response schema."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


class ApiError(Exception):
    """Expected domain or transport error that is safe to return to an operator."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers
        super().__init__(message)


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Create a schema-conformant JSON error response."""
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"X-Correlation-ID": request_id, **(headers or {})},
    )


def _request_id(request: Request) -> str:
    return cast(str, getattr(request.state, "correlation_id", get_correlation_id()))


def register_exception_handlers(app: FastAPI) -> None:
    """Register predictable, secret-safe error mappings on an application."""

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=_request_id(request),
            details=exc.details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Deliberately omit rejected values: API inputs can contain configurations or secrets.
        details = {
            "issues": [
                {"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()
            ]
        }
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="validation_error",
            message="The submitted request is invalid.",
            request_id=_request_id(request),
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
        )
        return error_response(
            status_code=exc.status_code,
            code="http_error",
            message=message,
            request_id=_request_id(request),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.exception("unhandled_exception", extra={"request_id": request_id})
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="An unexpected error occurred.",
            request_id=request_id,
        )
