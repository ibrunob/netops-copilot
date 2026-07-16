"""Authenticated upload-intent contract; it never accepts artifact bytes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from netops_api.api.cases import CaseRepositoryDependency
from netops_api.api.config_preview import _WRITE_ROLES, _require_visible_case
from netops_api.api.errors import ApiError, ErrorEnvelope
from netops_api.application.artifact_intents import (
    CreateArtifactUploadIntent,
    TenantArtifactIntentRepository,
)
from netops_api.application.artifacts import ArtifactStore, ArtifactUploadRequest
from netops_api.core.auth import AuthorizationError
from netops_api.core.dependencies import (
    ApplicationDependencies,
    PrincipalDependency,
    TenantConnectionDependency,
    get_dependencies,
)
from netops_api.core.request_context import get_correlation_id

router = APIRouter(prefix="/v1/cases", tags=["artifact uploads"])

_AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "A signed access token is required."},
    403: {"model": ErrorEnvelope, "description": "The signed user lacks case permission."},
}
_FILENAME_RE = re.compile(r"^[^/\\\x00-\x1f]{1,255}$")


class ArtifactUploadIntentRequest(BaseModel):
    """Strict declared metadata used to bind a one-time object-store capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_kind: Literal["network-configuration", "incident-audio"]
    content_type: str = Field(min_length=1, max_length=255)
    content_length: int = Field(ge=1, le=100 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_filename: str | None = Field(default=None, max_length=255)

    @field_validator("content_type")
    @classmethod
    def require_supported_content_type(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized != value or normalized not in {
            "text/plain",
            "application/json",
            "audio/mpeg",
            "audio/wav",
            "audio/x-wav",
            "audio/mp4",
            "audio/webm",
        }:
            raise ValueError("content_type is not permitted for artifact upload.")
        return normalized

    @field_validator("original_filename")
    @classmethod
    def require_safe_filename(cls, value: str | None) -> str | None:
        if value is not None and not _FILENAME_RE.fullmatch(value):
            raise ValueError(
                "original_filename must be a plain filename without control characters."
            )
        return value


class ArtifactUploadCapabilityResponse(BaseModel):
    """Short-lived capability. No bytes or object-store key are disclosed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    intent_id: UUID
    upload_url: str
    required_headers: dict[str, str]
    expires_at: datetime


def get_artifact_store(
    dependencies: Annotated[ApplicationDependencies, Depends(get_dependencies)],
) -> ArtifactStore:
    """Resolve an explicitly wired store, never silently falling back to a fake."""
    if dependencies.artifact_store is None:
        raise ApiError(
            status_code=503,
            code="artifact_storage_unavailable",
            message="Artifact upload storage is not configured for this API instance.",
        )
    return dependencies.artifact_store


ArtifactStoreDependency = Annotated[ArtifactStore, Depends(get_artifact_store)]


@router.post(
    "/{case_id}/artifacts/upload-intents",
    response_model=ArtifactUploadCapabilityResponse,
    responses={
        **_AUTH_ERROR_RESPONSES,
        404: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def create_upload_intent(
    case_id: UUID,
    body: ArtifactUploadIntentRequest,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
    connection: TenantConnectionDependency,
    store: ArtifactStoreDependency,
) -> ArtifactUploadCapabilityResponse:
    """Authorize and record metadata before returning a byte-free upload capability."""
    try:
        principal.require_any_role(*_WRITE_ROLES)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=403,
            code="case_write_forbidden",
            message="The signed-in user lacks a case-write role.",
        ) from exc
    _require_visible_case(case_id, principal, repository)
    if body.artifact_kind == "network-configuration" and body.content_type not in {
        "text/plain",
        "application/json",
    }:
        raise ApiError(
            422,
            "invalid_artifact_metadata",
            "Config artifacts require text/plain or application/json.",
        )
    if body.artifact_kind == "incident-audio" and not body.content_type.startswith("audio/"):
        raise ApiError(
            422,
            "invalid_artifact_metadata",
            "Audio artifacts require an audio content type.",
        )

    now = datetime.now(UTC)
    artifact_id = uuid4()
    intent_id = uuid4()
    upload_request = ArtifactUploadRequest(
        organization_id=principal.organization_id,
        artifact_id=artifact_id,
        case_id=case_id,
        content_type=body.content_type,
        content_length=body.content_length,
        sha256_hex=body.sha256,
    )
    try:
        capability = store.presign_upload(upload_request, now=now)
    except RuntimeError as exc:
        raise ApiError(
            503,
            "artifact_storage_unavailable",
            "Artifact upload storage is unavailable.",
        ) from exc
    actor_id = uuid5(NAMESPACE_URL, f"{principal.issuer}\x1f{principal.subject}")
    TenantArtifactIntentRepository(connection, principal.organization_id).create(
        CreateArtifactUploadIntent(
            intent_id=intent_id,
            artifact_id=artifact_id,
            case_id=case_id,
            artifact_kind=body.artifact_kind,
            classification="raw",
            content_type=body.content_type,
            byte_size=body.content_length,
            sha256_hex=body.sha256,
            original_filename=body.original_filename,
            actor_id=actor_id,
            correlation_id=UUID(get_correlation_id()),
            created_at=now,
            expires_at=capability.expires_at,
        )
    )
    return ArtifactUploadCapabilityResponse(
        artifact_id=artifact_id,
        intent_id=intent_id,
        upload_url=capability.upload_url,
        required_headers=dict(capability.required_headers),
        expires_at=capability.expires_at,
    )
