"""Dependency-injection boundary for HTTP routes and future infrastructure adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from netops_api.api.errors import ApiError
from netops_api.application.artifacts import ArtifactStore, MinioArtifactStore
from netops_api.core.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthenticationServiceError,
    AuthorizationError,
    JwtTokenVerifier,
)
from netops_api.core.config import Settings
from netops_api.core.database import TenantContextError, TenantDatabase
from netops_api.domain.cases import CaseRole


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    """Application-owned dependency container.

    Persistence and external clients are explicit typed ports rather than being
    instantiated from request handlers. The JWT verifier and tenant database
    ensure every organization-owned operation derives scope from one signed
    principal and opens a transaction with that scope before issuing SQL.
    """

    settings: Settings
    token_verifier: JwtTokenVerifier
    database: TenantDatabase | None
    artifact_store: ArtifactStore | None = None


def build_dependencies(settings: Settings) -> ApplicationDependencies:
    """Construct the service container at application startup."""
    artifact_store = None
    if settings.artifact_store.enabled:
        access_key_id = settings.artifact_store.access_key_id
        secret_access_key = settings.artifact_store.secret_access_key
        if access_key_id is None or secret_access_key is None:
            raise RuntimeError(
                "Artifact storage is enabled but MinIO credentials are not configured."
            )
        import boto3  # type: ignore[import-untyped]

        client_kwargs = {
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key.get_secret_value(),
            "config": boto3.session.Config(
                signature_version="s3v4", s3={"addressing_style": "path"}
            ),
            "region_name": "us-east-1",
        }
        presign_client = boto3.client(
            "s3",
            endpoint_url=settings.artifact_store.public_endpoint_url,
            **client_kwargs,
        )
        private_minio = settings.dependencies.minio
        head_client = boto3.client(
            "s3",
            endpoint_url=f"http://{private_minio.host}:{private_minio.port}",
            **client_kwargs,
        )
        artifact_store = MinioArtifactStore(
            client=presign_client,
            head_client=head_client,
            settings=settings.artifact_store,
        )
    return ApplicationDependencies(
        settings=settings,
        token_verifier=JwtTokenVerifier.from_settings(settings.auth),
        database=(
            TenantDatabase.from_url(settings.database_url.get_secret_value())
            if settings.database_url is not None
            else None
        ),
        artifact_store=artifact_store,
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
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)],
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


def get_tenant_connection(
    dependencies: Annotated[ApplicationDependencies, Depends(get_dependencies)],
    principal: PrincipalDependency,
) -> Generator[Connection, None, None]:
    """Open a database transaction scoped only from verified identity claims."""
    if dependencies.database is None:
        raise ApiError(
            status_code=503,
            code="persistence_unavailable",
            message="Tenant persistence is not configured for this API instance.",
        )
    try:
        with dependencies.database.tenant_connection(principal.organization_id) as connection:
            yield connection
    except (SQLAlchemyError, TenantContextError) as exc:
        raise ApiError(
            status_code=503,
            code="tenant_context_unavailable",
            message="Tenant persistence is temporarily unavailable.",
        ) from exc


TenantConnectionDependency = Annotated[Connection, Depends(get_tenant_connection)]


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
