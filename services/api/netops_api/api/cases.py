"""Authenticated, tenant-scoped HTTP contract for the case command spine.

Routes intentionally accept no organization or actor identifiers.  Both values
are derived from the signature-verified principal and each repository is built
inside the request's RLS-scoped transaction.
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from netops_api.api.errors import ApiError, ErrorEnvelope
from netops_api.application.cases import (
    CaseDetail,
    CaseInputCommand,
    CaseListCursor,
    CaseListPage,
    CaseNotFoundError,
    CasePersistence,
    CaseRecord,
    CaseService,
    CaseTimelineEntry,
    CreateCaseCommand,
    CreateCaseResult,
    IdempotencyConflictError,
    TenantCaseRepository,
)
from netops_api.core.auth import AuthenticatedPrincipal, AuthorizationError
from netops_api.core.dependencies import PrincipalDependency, TenantConnectionDependency
from netops_api.core.request_context import get_correlation_id
from netops_api.domain.cases import (
    Actor,
    ActorKind,
    CaseRole,
    CaseSnapshot,
    CaseState,
    TransitionCommand,
    TransitionOutcome,
)
from netops_api.domain.errors import (
    InvalidTransitionError,
    TransitionAuthorizationError,
    TransitionConstraintError,
    VersionConflictError,
)

router = APIRouter(prefix="/v1/cases", tags=["cases"])

_CASE_CREATE_ROLES = frozenset(
    {CaseRole.ORG_ADMIN, CaseRole.OPERATOR, CaseRole.APPROVER, CaseRole.PLATFORM_ADMIN}
)
# All product roles may inspect an organization-wide, assetless case.  Asset-bound
# cases are additionally hidden unless their exact asset ID is present in the token.
_CASE_READ_ROLES: frozenset[CaseRole] = frozenset(role for role in CaseRole)
_AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "A signed access token is required."},
    403: {"model": ErrorEnvelope, "description": "The signed user lacks case permission."},
}


class CaseRepository(CasePersistence, Protocol):
    """The persistence operations exposed to the HTTP adapter."""

    def create_case(self, command: CreateCaseCommand) -> CreateCaseResult: ...

    def list_cases(
        self,
        *,
        asset_ids: tuple[UUID, ...] = (),
        limit: int = 50,
        cursor: CaseListCursor | None = None,
        query: str | None = None,
        state: CaseState | None = None,
        severity: str | None = None,
    ) -> CaseListPage: ...

    def get_detail(self, case_id: UUID) -> CaseDetail: ...

    def get_snapshot(self, case_id: UUID) -> CaseSnapshot: ...

    def persist_transition(self, outcome: TransitionOutcome, actor: Actor) -> CaseRecord: ...


def get_case_repository(
    principal: PrincipalDependency,
    connection: TenantConnectionDependency,
) -> TenantCaseRepository:
    """Build a repository from the verified tenant and transaction only."""
    return TenantCaseRepository(connection, principal.organization_id)


CaseRepositoryDependency = Annotated[CaseRepository, Depends(get_case_repository)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


class CaseInputRequest(BaseModel):
    """Operator input captured immutably at case creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_kind: str = Field(min_length=1, max_length=100)
    content: dict[str, JsonValue]


class CreateCaseRequest(BaseModel):
    """Client-supplied case intent; identity/scope remain server-derived."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    severity: Literal["low", "medium", "high", "critical"]
    asset_id: UUID | None = None
    input: CaseInputRequest | None = None


class TransitionCaseRequest(BaseModel):
    """A compare-and-swap state transition request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int = Field(ge=0)
    to_state: CaseState
    approval_id: UUID | None = None
    verification_note: str | None = Field(default=None, min_length=1, max_length=10_000)
    knowledge_item_id: UUID | None = None
    note: str | None = Field(default=None, min_length=1, max_length=10_000)


class ResolveCaseRequest(BaseModel):
    """Evidence-bearing explicit resolution action for a confirmed case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int = Field(ge=0)
    verification_note: str = Field(min_length=1, max_length=10_000)


class CaseFeedbackRequest(BaseModel):
    """Explicit operator feedback requesting additional case information."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=10_000)


class CaseResponse(BaseModel):
    """Tenant-authorized current case projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    state: CaseState
    version: int
    title: str
    category: str | None
    severity: str
    asset_id: UUID | None
    created_by_actor_id: UUID | None
    created_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    """Stable tenant-visible case list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CaseResponse, ...]
    next_cursor: str | None = None


class CaseTimelineEntryResponse(BaseModel):
    """One immutable timeline fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    event_type: str
    aggregate_version: int
    transition_id: UUID | None
    actor_id: UUID
    correlation_id: UUID
    occurred_at: datetime
    from_state: CaseState | None
    to_state: CaseState | None
    approval_id: UUID | None
    verification_note: str | None
    knowledge_item_id: UUID | None
    note: str | None


class CaseDetailResponse(BaseModel):
    """Case projection and its immutable operator timeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case: CaseResponse
    timeline: tuple[CaseTimelineEntryResponse, ...]


def _actor_from_principal(principal: AuthenticatedPrincipal) -> Actor:
    """Create a stable opaque actor ID without assuming ``sub`` is a UUID."""
    try:
        return Actor(
            actor_id=uuid5(NAMESPACE_URL, f"{principal.issuer}\x1f{principal.subject}"),
            kind=ActorKind.HUMAN,
            roles=principal.roles,
        )
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="case_write_forbidden",
            message="The signed-in user lacks a case-write role.",
        ) from exc


def _correlation_id() -> UUID:
    """Use the middleware-validated request correlation ID in every command."""
    return UUID(get_correlation_id())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _encode_case_list_cursor(cursor: CaseListCursor) -> str:
    """Encode only the stable ordering key; the cursor carries no tenant data."""
    payload = json.dumps(
        {"case_id": str(cursor.case_id), "updated_at": cursor.updated_at.isoformat()},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _parse_case_list_cursor(value: str) -> CaseListCursor:
    """Decode a client cursor strictly, without reflecting it in error output."""
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(urlsafe_b64decode(padded.encode("ascii")))
        if set(payload) != {"case_id", "updated_at"}:
            raise ValueError("Unexpected cursor members.")
        updated_at = datetime.fromisoformat(payload["updated_at"])
        if updated_at.tzinfo is None or updated_at.utcoffset() != UTC.utcoffset(updated_at):
            raise ValueError("Cursor time must use UTC.")
        return CaseListCursor(
            case_id=UUID(payload["case_id"]),
            updated_at=updated_at.astimezone(UTC),
        )
    except (BinasciiError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_case_cursor",
            message="The case list cursor is invalid.",
        ) from exc


def _require_create_asset_scope(principal: AuthenticatedPrincipal, asset_id: UUID | None) -> None:
    if asset_id is None:
        return
    try:
        principal.require_asset(asset_id)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="asset_scope_denied",
            message="The signed-in user cannot create a case for this asset.",
        ) from exc


def _require_case_read_access(principal: AuthenticatedPrincipal) -> None:
    try:
        principal.require_any_role(*_CASE_READ_ROLES)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="case_read_forbidden",
            message="The signed-in user lacks a case-read role.",
        ) from exc


def _require_case_create_access(principal: AuthenticatedPrincipal) -> None:
    try:
        principal.require_any_role(*_CASE_CREATE_ROLES)
    except AuthorizationError as exc:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="case_write_forbidden",
            message="The signed-in user lacks a case-write role.",
        ) from exc


def _is_visible_to_principal(case: CaseRecord, principal: AuthenticatedPrincipal) -> bool:
    """Apply the intentional assetless/asset-bound case read policy.

    Assetless cases are organization-level operational records, available to a
    signed principal that passed the case-read role gate. An asset-bound case is
    visible only when that exact asset ID appears in the verified token scope;
    empty asset scope therefore grants no asset-bound case visibility.
    """
    return case.asset_id is None or case.asset_id in principal.asset_ids


def _visible_case_or_not_found(case: CaseRecord, principal: AuthenticatedPrincipal) -> CaseRecord:
    if not _is_visible_to_principal(case, principal):
        raise _case_not_found()
    return case


def _case_not_found() -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="case_not_found",
        message="The requested case was not found.",
    )


def _case_response(case: CaseRecord) -> CaseResponse:
    return CaseResponse(
        id=case.case_id,
        state=case.state,
        version=case.version,
        title=case.title,
        category=case.category,
        severity=case.severity,
        asset_id=case.asset_id,
        created_by_actor_id=case.created_by_actor_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _timeline_response(entry: CaseTimelineEntry) -> CaseTimelineEntryResponse:
    return CaseTimelineEntryResponse(
        event_id=entry.event_id,
        event_type=entry.event_type,
        aggregate_version=entry.aggregate_version,
        transition_id=entry.transition_id,
        actor_id=entry.actor_id,
        correlation_id=entry.correlation_id,
        occurred_at=entry.occurred_at,
        from_state=entry.from_state,
        to_state=entry.to_state,
        approval_id=entry.approval_id,
        verification_note=entry.verification_note,
        knowledge_item_id=entry.knowledge_item_id,
        note=entry.note,
    )


def _map_case_error(exc: Exception) -> ApiError:
    """Map expected state/persistence failures without exposing implementation details."""
    if isinstance(exc, CaseNotFoundError):
        return _case_not_found()
    if isinstance(exc, IdempotencyConflictError):
        return ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="idempotency_conflict",
            message="The idempotency key was already used for a different request.",
        )
    if isinstance(exc, VersionConflictError):
        return ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="case_version_conflict",
            message="The case changed before this transition could be applied.",
        )
    if isinstance(exc, TransitionAuthorizationError):
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="case_transition_forbidden",
            message="The signed-in user cannot perform this case transition.",
        )
    if isinstance(exc, InvalidTransitionError | TransitionConstraintError | ValueError):
        return ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_case_transition",
            message="The requested case operation is invalid.",
        )
    raise exc


@router.post(
    "",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_AUTH_ERROR_RESPONSES,
        200: {"model": CaseResponse, "description": "Idempotent replay of an existing case."},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def create_case(
    body: CreateCaseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
    response: Response,
) -> CaseResponse:
    """Create a case once, recording immutable creation lineage and an outbox event."""
    if not idempotency_key.strip():
        raise _map_case_error(ValueError("idempotency key cannot be blank"))
    _require_case_create_access(principal)
    _require_create_asset_scope(principal, body.asset_id)
    actor = _actor_from_principal(principal)
    case_input = None
    if body.input is not None:
        canonical_content = json.dumps(body.input.content, sort_keys=True, separators=(",", ":"))
        case_input = CaseInputCommand(
            input_id=uuid4(),
            input_kind=body.input.input_kind,
            content_sha256=sha256(canonical_content.encode("utf-8")).hexdigest(),
            content=body.input.content,
        )
    command = CreateCaseCommand(
        case_id=uuid4(),
        event_id=uuid4(),
        idempotency_key=idempotency_key,
        title=body.title,
        category=body.category,
        severity=body.severity,
        asset_id=body.asset_id,
        actor=actor,
        correlation_id=_correlation_id(),
        occurred_at=_utc_now(),
        case_input=case_input,
    )
    try:
        result = repository.create_case(command)
    except Exception as exc:
        raise _map_case_error(exc) from exc
    if not result.created:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
    return _case_response(result.case)


@router.get(
    "",
    response_model=CaseListResponse,
    responses={**_AUTH_ERROR_RESPONSES, 422: {"model": ErrorEnvelope}},
)
def list_cases(
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    state: CaseState | None = None,
    severity: Annotated[Literal["low", "medium", "high", "critical"] | None, Query()] = None,
) -> CaseListResponse:
    """List a filtered, cursor-paginated case queue visible to the signed scope."""
    _require_case_read_access(principal)
    parsed_cursor = _parse_case_list_cursor(cursor) if cursor is not None else None
    try:
        page = repository.list_cases(
            asset_ids=tuple(principal.asset_ids),
            limit=limit,
            cursor=parsed_cursor,
            query=q,
            state=state,
            severity=severity,
        )
    except Exception as exc:
        raise _map_case_error(exc) from exc
    return CaseListResponse(
        items=tuple(_case_response(case) for case in page.items),
        next_cursor=(
            _encode_case_list_cursor(page.next_cursor) if page.next_cursor is not None else None
        ),
    )


@router.get(
    "/{case_id}",
    response_model=CaseDetailResponse,
    responses={**_AUTH_ERROR_RESPONSES, 404: {"model": ErrorEnvelope}},
)
def get_case(
    case_id: UUID,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> CaseDetailResponse:
    """Get an asset-authorized case projection and immutable timeline."""
    _require_case_read_access(principal)
    try:
        detail = repository.get_detail(case_id)
        case = _visible_case_or_not_found(detail.case, principal)
    except ApiError:
        raise
    except Exception as exc:
        raise _map_case_error(exc) from exc
    return CaseDetailResponse(
        case=_case_response(case),
        timeline=tuple(_timeline_response(entry) for entry in detail.timeline),
    )


@router.get(
    "/{case_id}/timeline",
    response_model=tuple[CaseTimelineEntryResponse, ...],
    responses={**_AUTH_ERROR_RESPONSES, 404: {"model": ErrorEnvelope}},
)
def get_case_timeline(
    case_id: UUID,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> tuple[CaseTimelineEntryResponse, ...]:
    """Return immutable timeline entries after checking the case asset scope."""
    _require_case_read_access(principal)
    try:
        detail = repository.get_detail(case_id)
        _visible_case_or_not_found(detail.case, principal)
    except ApiError:
        raise
    except Exception as exc:
        raise _map_case_error(exc) from exc
    return tuple(_timeline_response(entry) for entry in detail.timeline)


@router.post(
    "/{case_id}/transitions",
    response_model=CaseResponse,
    responses={
        **_AUTH_ERROR_RESPONSES,
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def transition_case(
    case_id: UUID,
    body: TransitionCaseRequest,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> CaseResponse:
    """Apply one authorized compare-and-swap state transition."""
    return _apply_transition(
        case_id=case_id,
        expected_version=body.expected_version,
        to_state=body.to_state,
        principal=principal,
        repository=repository,
        approval_id=body.approval_id,
        verification_note=body.verification_note,
        knowledge_item_id=body.knowledge_item_id,
        note=body.note,
    )


@router.post(
    "/{case_id}/resolution",
    response_model=CaseResponse,
    responses={
        **_AUTH_ERROR_RESPONSES,
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def resolve_case(
    case_id: UUID,
    body: ResolveCaseRequest,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> CaseResponse:
    """Resolve a confirmed case with a mandatory human verification note."""
    return _apply_transition(
        case_id=case_id,
        expected_version=body.expected_version,
        to_state=CaseState.RESOLVED,
        principal=principal,
        repository=repository,
        verification_note=body.verification_note,
    )


@router.post(
    "/{case_id}/feedback",
    response_model=CaseResponse,
    responses={
        **_AUTH_ERROR_RESPONSES,
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def request_case_feedback(
    case_id: UUID,
    body: CaseFeedbackRequest,
    principal: PrincipalDependency,
    repository: CaseRepositoryDependency,
) -> CaseResponse:
    """Record an operator-visible need for additional case information."""
    return _apply_transition(
        case_id=case_id,
        expected_version=body.expected_version,
        to_state=CaseState.NEEDS_INFORMATION,
        principal=principal,
        repository=repository,
        note=body.note,
    )


def _apply_transition(
    *,
    case_id: UUID,
    expected_version: int,
    to_state: CaseState,
    principal: AuthenticatedPrincipal,
    repository: CaseRepository,
    approval_id: UUID | None = None,
    verification_note: str | None = None,
    knowledge_item_id: UUID | None = None,
    note: str | None = None,
) -> CaseResponse:
    """Build and execute a state command shared by generic and explicit actions."""
    _require_case_read_access(principal)
    _require_case_create_access(principal)
    try:
        detail = repository.get_detail(case_id)
        _visible_case_or_not_found(detail.case, principal)
        actor = _actor_from_principal(principal)
        command = TransitionCommand(
            transition_id=uuid4(),
            event_id=uuid4(),
            case_id=case_id,
            expected_version=expected_version,
            to_state=to_state,
            actor=actor,
            correlation_id=_correlation_id(),
            occurred_at=_utc_now(),
            approval_id=approval_id,
            verification_note=verification_note,
            knowledge_item_id=knowledge_item_id,
            note=note,
        )
        updated = CaseService(repository).transition(command)
    except ApiError:
        raise
    except Exception as exc:
        raise _map_case_error(exc) from exc
    return _case_response(updated)
