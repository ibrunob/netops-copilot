"""Authenticated identity introspection for the web session boundary."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from netops_api.core.dependencies import PrincipalDependency
from netops_api.domain.cases import CaseRole

router = APIRouter(prefix="/v1/auth", tags=["identity"])


class AuthenticatedIdentityResponse(BaseModel):
    """The signed claims application routes may safely use for authorization UX."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    organization_id: UUID
    roles: tuple[CaseRole, ...]
    asset_ids: tuple[UUID, ...]


@router.get("/me", response_model=AuthenticatedIdentityResponse)
async def get_authenticated_identity(
    principal: PrincipalDependency,
) -> AuthenticatedIdentityResponse:
    """Return the verified identity; no client-provided tenant value is accepted."""
    return AuthenticatedIdentityResponse(
        subject=principal.subject,
        organization_id=principal.organization_id,
        roles=tuple(sorted(principal.roles, key=str)),
        asset_ids=tuple(sorted(principal.asset_ids, key=str)),
    )
