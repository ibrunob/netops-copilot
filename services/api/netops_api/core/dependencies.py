"""Dependency-injection boundary for HTTP routes and future infrastructure adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from netops_api.core.config import Settings


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    """Application-owned dependency container.

    Persistence, authorization, and external clients will be added here as explicit typed
    ports rather than instantiated from request handlers.
    """

    settings: Settings


def build_dependencies(settings: Settings) -> ApplicationDependencies:
    """Construct the service container at application startup."""
    return ApplicationDependencies(settings=settings)


def get_dependencies(request: Request) -> ApplicationDependencies:
    """Resolve the app container for route-level dependency injection."""
    return cast(ApplicationDependencies, request.app.state.dependencies)


def get_settings_dependency(
    dependencies: Annotated[ApplicationDependencies, Depends(get_dependencies)],
) -> Settings:
    """Resolve settings through the container rather than reading the environment in routes."""
    return dependencies.settings
