"""Authenticated, non-persistent Cisco configuration preview endpoint."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from netops_api.api.cases import CaseRepositoryDependency
from netops_api.api.errors import ApiError, ErrorEnvelope
from netops_api.application.cases import CaseNotFoundError
from netops_api.application.config_preview import (
    CONFIG_PREVIEW_MAX_BYTES,
    CONFIG_PREVIEW_MAX_LINES,
    ConfigPreviewLimitError,
    preview_cisco_config,
)
from netops_api.core.auth import AuthorizationError
from netops_api.core.dependencies import PrincipalDependency
from netops_api.domain.cases import CaseRole
from netops_api.ingestion.redaction import RedactionReport

router = APIRouter(prefix="/v1/cases", tags=["config previews"])

_WRITE_ROLES = frozenset(
    {CaseRole.ORG_ADMIN, CaseRole.OPERATOR, CaseRole.APPROVER, CaseRole.PLATFORM_ADMIN}
)
_READ_ROLES: frozenset[CaseRole] = frozenset(role for role in CaseRole)
_AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "A signed access token is required."},
    403: {"model": ErrorEnvelope, "description": "The signed user lacks case permission."},
}


class ConfigPreviewRequest(BaseModel):
    """Raw config stays request-local and is never recorded in an event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config: str = Field(min_length=1, description="Cisco-style configuration paste for redaction.")


class RedactionRuleSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    line_count: int
    occurrence_count: int


class RedactionReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_line_count: int
    redacted_line_count: int
    rules: tuple[RedactionRuleSummaryResponse, ...]


class ConfigPreviewResponse(BaseModel):
    """Redacted-only derivative; raw config is neither persisted nor disclosed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    redacted_content: str
    redacted_content_sha256: str
    redaction_version: str
    report: RedactionReportResponse


def _require_preview_access(principal: PrincipalDependency) -> None:
    try:
        principal.require_any_role(*_READ_ROLES)
        principal.require_any_role(*_WRITE_ROLES)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=403,
            code="case_write_forbidden",
            message="The signed-in user lacks a case-write role.",
        ) from exc


def _require_visible_case(
    case_id: UUID,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> None:
    """Perform tenant repository and exact asset-scope checks before touching raw input."""
    try:
        detail = repository.get_detail(case_id)
    except CaseNotFoundError as exc:
        raise _case_not_found() from exc
    if detail.case.asset_id is not None and detail.case.asset_id not in principal.asset_ids:
        raise _case_not_found()


def _case_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="case_not_found",
        message="The requested case was not found.",
    )


def _report_response(report: RedactionReport) -> RedactionReportResponse:
    return RedactionReportResponse(
        source_line_count=report.source_line_count,
        redacted_line_count=report.redacted_line_count,
        rules=tuple(
            RedactionRuleSummaryResponse(
                rule_id=rule.rule_id,
                line_count=rule.line_count,
                occurrence_count=rule.occurrence_count,
            )
            for rule in report.rules
        ),
    )


@router.post(
    "/{case_id}/config-preview",
    response_model=ConfigPreviewResponse,
    responses={
        **_AUTH_ERROR_RESPONSES,
        404: {"model": ErrorEnvelope},
        413: {"model": ErrorEnvelope, "description": "Config paste exceeds preview limits."},
        422: {"model": ErrorEnvelope},
    },
)
def preview_case_config(
    case_id: UUID,
    body: ConfigPreviewRequest,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> ConfigPreviewResponse:
    """Return a bounded redacted Cisco preview without persistence or event emission."""
    _require_preview_access(principal)
    _require_visible_case(case_id, principal, repository)
    try:
        preview = preview_cisco_config(body.config)
    except ConfigPreviewLimitError as exc:
        raise ApiError(
            status_code=413,
            code="config_preview_limit_exceeded",
            message=(
                "The config preview exceeds the maximum of "
                f"{CONFIG_PREVIEW_MAX_BYTES} bytes or {CONFIG_PREVIEW_MAX_LINES} lines."
            ),
        ) from exc
    return ConfigPreviewResponse(
        redacted_content=preview.redacted_content,
        redacted_content_sha256=preview.redacted_content_sha256,
        redaction_version=preview.redaction_version,
        report=_report_response(preview.report),
    )
