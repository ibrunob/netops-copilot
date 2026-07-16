"""FastAPI application assembly and local server entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from netops_api.api.artifact_status import router as artifact_status_router
from netops_api.api.artifact_upload_completion import router as artifact_upload_completion_router
from netops_api.api.artifact_uploads import router as artifact_uploads_router
from netops_api.api.cases import router as cases_router
from netops_api.api.config_preview import router as config_preview_router
from netops_api.api.errors import register_exception_handlers
from netops_api.api.events import router as events_router
from netops_api.api.health import router as health_router
from netops_api.api.identity import router as identity_router
from netops_api.core.config import Settings, get_settings
from netops_api.core.dependencies import ApplicationDependencies, build_dependencies
from netops_api.core.logging import configure_logging
from netops_api.core.middleware import (
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    RequestTelemetryMiddleware,
)
from netops_api.core.observability import configure_opentelemetry
from netops_api.core.sentry import configure_sentry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the API application with explicit, replaceable runtime dependencies."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    dependencies = build_dependencies(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("application_started", extra={"service": resolved_settings.service_name})
        try:
            yield
        finally:
            if dependencies.database is not None:
                dependencies.database.dispose()
            logger.info("application_stopped", extra={"service": resolved_settings.service_name})

    app = FastAPI(
        title="NetOps Copilot API",
        version=resolved_settings.api_version,
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved_settings.docs_enabled else None,
    )
    app.state.dependencies = dependencies

    # Middleware is installed inside-out: correlation context remains outermost so
    # throttled responses have a stable request ID and W3C trace context.
    app.add_middleware(RequestTelemetryMiddleware)
    app.add_middleware(RateLimitMiddleware, settings=resolved_settings)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(cases_router)
    app.include_router(artifact_uploads_router)
    app.include_router(artifact_upload_completion_router)
    app.include_router(artifact_status_router)
    app.include_router(config_preview_router)
    app.include_router(events_router)
    configure_opentelemetry(app, resolved_settings)
    configure_sentry(resolved_settings)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": resolved_settings.service_name,
                "version": resolved_settings.api_version,
            }
        )

    return app


def get_application_dependencies(app: FastAPI) -> ApplicationDependencies:
    """Expose the typed app container for infrastructure and application wiring."""
    return cast(ApplicationDependencies, app.state.dependencies)


def run() -> None:
    """Run the local ASGI server using environment-backed settings."""
    settings = get_settings()
    uvicorn.run(
        "netops_api.main:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        log_config=None,
        reload=settings.reload,
    )
