"""Unauthenticated liveness and process-readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from netops_api.api.errors import ApiError
from netops_api.core.config import Settings
from netops_api.core.database import TenantContextError
from netops_api.core.dependencies import (
    ApplicationDependencies,
    get_dependencies,
    get_settings_dependency,
)

router = APIRouter(tags=["platform"])
SettingsDependency = Annotated[Settings, Depends(get_settings_dependency)]
DependenciesDependency = Annotated[ApplicationDependencies, Depends(get_dependencies)]


class HealthResponse(BaseModel):
    """Minimal response intended for load balancers and orchestrators."""

    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    version: str


class ReadinessResponse(HealthResponse):
    """Readiness result with explicit dependency component state."""

    components: dict[str, str]


@router.get("/healthz", response_model=HealthResponse, include_in_schema=False)
async def liveness(settings: SettingsDependency) -> HealthResponse:
    """Report whether this process can serve requests."""
    return HealthResponse(status="ok", service=settings.service_name, version=settings.api_version)


@router.get("/readyz", response_model=ReadinessResponse, include_in_schema=False)
def readiness(
    settings: SettingsDependency, dependencies: DependenciesDependency
) -> ReadinessResponse:
    """Report whether the process and its configured persistence are ready.

    FastAPI executes this synchronous handler in its worker thread pool. That
    keeps a bounded database probe from blocking the event loop.
    """
    components = {"application": "ready"}
    if dependencies.database is not None:
        try:
            dependencies.database.check_readiness()
        except (SQLAlchemyError, TenantContextError) as exc:
            raise ApiError(
                status_code=503,
                code="persistence_unavailable",
                message="The application database is temporarily unavailable.",
            ) from exc
        components["database"] = "ready"
    return ReadinessResponse(
        status="ok",
        service=settings.service_name,
        version=settings.api_version,
        components=components,
    )
