"""Unauthenticated liveness and process-readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from netops_api.core.config import Settings
from netops_api.core.dependencies import get_settings_dependency

router = APIRouter(tags=["platform"])
SettingsDependency = Annotated[Settings, Depends(get_settings_dependency)]


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
async def readiness(settings: SettingsDependency) -> ReadinessResponse:
    """Report application readiness before external dependencies are introduced."""
    return ReadinessResponse(
        status="ok",
        service=settings.service_name,
        version=settings.api_version,
        components={"application": "ready"},
    )
