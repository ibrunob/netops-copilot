"""Dependency-injection boundary for HTTP routes and future infrastructure adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from netops_api.api.errors import ApiError
from netops_api.core.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthenticationServiceError,
    AuthorizationError,
    JwtTokenVerifier,
)
from netops_api.core.config import Settings
from netops_api.domain.cases import CaseRole


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    """Application-owned dependency container.

    Persistence and external clients are added here as explicit typed ports rather
    than instantiated from request handlers. The JWT verifier is present now so
    every future route derives tenant scope from the same signed principal.
    """

    settings: Settings
    token_verifier: JwtTokenVerifier


def build_dependencies(settings: Settings) -> ApplicationDependencies:
    """Construct the service container at application startup."""
    return ApplicationDependencies(
        settings=settings,
        token_verifier=JwtTokenVerifier.from_settings(settings.auth),
    )


def get_dependencies(request: Request) -> ApplicationDependencies:
    """Resolve the app container for route-level dependency injection."""
    return cast(ApplicationDependencies, request.app.state.dependencies)


def get_settings_dependency(
    dependencies: Annotated[ApplicationDependencies, Depends(get_dependencies)],
) -> Settings:
    """Resolve settings through the container rather than reading the environment in routes."""
    return dependencies.settings


_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="OIDC access token",
    description="A signed OIDC access token issued for the NetOps API audience.",
)


async def get_current_principal(
    dependencies: Annotated[ApplicationDependencies, Depends(get_dependencies)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
    ],
) -> AuthenticatedPrincipal:
    """Verify a bearer access token and return its tenant-bound principal."""
    if credentials is None or not credentials.credentials:
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="A signed access token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await dependencies.token_verifier.verify(credentials.credentials)
    except AuthenticationError as exc:
        raise ApiError(
            status_code=401,
            code=exc.code,
            message="The access token is invalid or expired.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except AuthenticationServiceError as exc:
        raise ApiError(
            status_code=503,
            code="authentication_unavailable",
            message="Authentication verification is temporarily unavailable.",
        ) from exc


PrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]


def require_roles(
    *roles: CaseRole,
) -> Callable[[AuthenticatedPrincipal], Awaitable[AuthenticatedPrincipal]]:
    """Create a route dependency enforcing one of the supplied product roles."""

    async def require_role(principal: PrincipalDependency) -> AuthenticatedPrincipal:
        try:
            principal.require_any_role(*roles)
        except AuthorizationError as exc:
            raise ApiError(
                status_code=403,
                code=exc.code,
                message="The signed-in user lacks the required permission.",
            ) from exc
        return principal

    return require_role
