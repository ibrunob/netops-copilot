"""Authenticated completion for metadata-verified artifact uploads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from netops_api.api.artifact_uploads import ArtifactStoreDependency
from netops_api.api.cases import CaseRepositoryDependency
from netops_api.api.config_preview import _WRITE_ROLES, _case_not_found, _require_visible_case
from netops_api.api.errors import ApiError, ErrorEnvelope
from netops_api.application.artifact_intents import (
    ArtifactUploadIntentExpiredError,
    ArtifactUploadIntentNotFoundError,
    ArtifactUploadVerificationError,
    TenantArtifactIntentRepository,
)
from netops_api.application.artifacts import ArtifactObjectMissingError
from netops_api.core.auth import AuthorizationError
from netops_api.core.dependencies import PrincipalDependency, TenantConnectionDependency

router = APIRouter(prefix="/v1/cases", tags=["artifact uploads"])

_AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "A signed access token is required."},
    403: {"model": ErrorEnvelope, "description": "The signed user lacks case permission."},
}


class ArtifactUploadCompletionResponse(BaseModel):
    """Safe result of a HEAD-only verification; no object locator or bytes leak."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    completed_at: datetime
    already_completed: bool


@router.post(
    "/{case_id}/artifacts/upload-intents/{intent_id}/complete",
    response_model=ArtifactUploadCompletionResponse,
    responses={
        **_AUTH_ERROR_RESPONSES,
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def complete_upload_intent(
    case_id: UUID,
    intent_id: UUID,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
    connection: TenantConnectionDependency,
    store: ArtifactStoreDependency,
) -> ArtifactUploadCompletionResponse:
    """Finalize only a tenant-visible, write-authorized, exact metadata match."""
    try:
        principal.require_any_role(*_WRITE_ROLES)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=403,
            code="case_write_forbidden",
            message="The signed-in user lacks a case-write role.",
        ) from exc
    _require_visible_case(case_id, principal, repository)
    try:
        result = TenantArtifactIntentRepository(connection, principal.organization_id).complete(
            case_id=case_id,
            intent_id=intent_id,
            store=store,
            now=datetime.now(UTC),
        )
    except ArtifactUploadIntentNotFoundError as exc:
        raise _case_not_found() from exc
    except ArtifactUploadIntentExpiredError as exc:
        raise ApiError(
            409, "artifact_upload_intent_expired", "The upload intent has expired."
        ) from exc
    except ArtifactObjectMissingError as exc:
        raise ApiError(
            409,
            "artifact_upload_not_found",
            "No uploaded object is available for this intent yet.",
        ) from exc
    except ArtifactUploadVerificationError as exc:
        raise ApiError(
            409,
            "artifact_upload_verification_failed",
            "Uploaded object metadata did not match the signed declaration.",
        ) from exc
    except RuntimeError as exc:
        raise ApiError(
            503,
            "artifact_storage_unavailable",
            "Artifact upload storage is unavailable.",
        ) from exc
    return ArtifactUploadCompletionResponse(
        artifact_id=result.artifact_id,
        completed_at=result.completed_at,
        already_completed=result.already_completed,
    )
