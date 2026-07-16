"""Case artifact lifecycle status; this contract exposes no artifact data."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from netops_api.api.cases import _CASE_READ_ROLES, CaseRepositoryDependency
from netops_api.api.config_preview import _require_visible_case
from netops_api.api.errors import ApiError, ErrorEnvelope
from netops_api.application.artifact_status import TenantArtifactStatusRepository
from netops_api.core.auth import AuthorizationError
from netops_api.core.dependencies import PrincipalDependency, TenantConnectionDependency

router = APIRouter(prefix="/v1/cases", tags=["artifact status"])

_AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "A signed access token is required."},
    403: {"model": ErrorEnvelope, "description": "The signed user lacks case permission."},
}


class ArtifactStatusResponse(BaseModel):
    """Safe lifecycle facts only, intentionally omitting all artifact metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    artifact_kind: str
    status: str
    status_updated_at: datetime


class CaseArtifactStatusListResponse(BaseModel):
    """Case-scoped artifact lifecycle read model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ArtifactStatusResponse, ...]


@router.get(
    "/{case_id}/artifacts/status",
    response_model=CaseArtifactStatusListResponse,
    responses={**_AUTH_ERROR_RESPONSES, 404: {"model": ErrorEnvelope}},
)
def list_case_artifact_statuses(
    case_id: UUID,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
    connection: TenantConnectionDependency,
) -> CaseArtifactStatusListResponse:
    """Read only lifecycle status after tenant, case, and asset scope are proven."""
    try:
        principal.require_any_role(*_CASE_READ_ROLES)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=403,
            code="case_read_forbidden",
            message="The signed-in user lacks a case-read role.",
        ) from exc
    _require_visible_case(case_id, principal, repository)
    statuses = TenantArtifactStatusRepository(connection, principal.organization_id).list_for_case(
        case_id
    )
    return CaseArtifactStatusListResponse(
        items=tuple(
            ArtifactStatusResponse(
                artifact_id=status.artifact_id,
                artifact_kind=status.artifact_kind,
                status=status.status,
                status_updated_at=status.status_updated_at,
            )
            for status in statuses
        )
    )
