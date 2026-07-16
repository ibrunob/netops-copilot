"""HTTP contracts for tenant-scoped case commands and reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.api.cases import _encode_case_list_cursor, get_case_repository
from netops_api.application.cases import (
    CaseDetail,
    CaseListCursor,
    CaseListPage,
    CaseNotFoundError,
    CaseRecord,
    CaseTimelineEntry,
    CreateCaseCommand,
    CreateCaseResult,
)
from netops_api.core.auth import AuthenticatedPrincipal
from netops_api.core.dependencies import get_current_principal
from netops_api.domain.cases import (
    Actor,
    CaseRole,
    CaseSnapshot,
    CaseState,
    TransitionOutcome,
)

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667701")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667702")
OTHER_ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667703")
CASE_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667704")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _principal(
    *,
    asset_ids: frozenset[UUID] = frozenset({ASSET_ID}),
    roles: frozenset[CaseRole] = frozenset({CaseRole.OPERATOR}),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="operator-1",
        organization_id=ORGANIZATION_ID,
        roles=roles,
        asset_ids=asset_ids,
        issuer="https://issuer.example.test/realms/netops",
        client_id="netops-web",
    )


def _case(
    *, asset_id: UUID | None = None, version: int = 0, state: CaseState | None = None
) -> CaseRecord:
    return CaseRecord(
        case_id=CASE_ID,
        state=state or (CaseState.NEW if version == 0 else CaseState.INVESTIGATING),
        version=version,
        title="VPN tunnel unavailable",
        category="ipsec",
        severity="high",
        asset_id=asset_id,
        created_by_actor_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class FakeCaseRepository:
    created: list[CreateCaseCommand] = field(default_factory=list)
    create_result_created: bool = True
    cases: tuple[CaseRecord, ...] = ()
    detail: CaseDetail | None = None
    snapshot: CaseSnapshot | None = None
    persisted: TransitionOutcome | None = None
    list_arguments: dict[str, object] | None = None

    def create_case(self, command: CreateCaseCommand) -> CreateCaseResult:
        self.created.append(command)
        return CreateCaseResult(
            case=_case(asset_id=command.asset_id),
            created=self.create_result_created,
        )

    def list_cases(
        self,
        *,
        asset_ids: tuple[UUID, ...] = (),
        limit: int = 50,
        cursor: CaseListCursor | None = None,
        query: str | None = None,
        state: CaseState | None = None,
        severity: str | None = None,
    ) -> CaseListPage:
        self.list_arguments = {
            "asset_ids": asset_ids,
            "cursor": cursor,
            "limit": limit,
            "query": query,
            "severity": severity,
            "state": state,
        }
        visible = tuple(
            case for case in self.cases if case.asset_id is None or case.asset_id in asset_ids
        )
        filtered = tuple(
            case
            for case in visible
            if (
                query is None
                or query.lower()
                in f"{case.title} {case.category or ''} {case.case_id}".lower()
            )
            and (state is None or case.state is state)
            and (severity is None or case.severity == severity)
        )
        return CaseListPage(items=filtered[:limit], next_cursor=None)

    def get_detail(self, case_id: UUID) -> CaseDetail:
        if self.detail is None or self.detail.case.case_id != case_id:
            raise CaseNotFoundError(case_id)
        return self.detail

    def get_snapshot(self, case_id: UUID) -> CaseSnapshot:
        if self.snapshot is None or self.snapshot.case_id != case_id:
            raise CaseNotFoundError(case_id)
        return self.snapshot

    def persist_transition(self, outcome: TransitionOutcome, actor: Actor) -> CaseRecord:
        self.persisted = outcome
        return _case(
            asset_id=self.detail.case.asset_id if self.detail else None,
            version=outcome.snapshot.version,
        )


@pytest.fixture
def case_repository() -> FakeCaseRepository:
    return FakeCaseRepository()


@pytest.fixture
def authenticated_client(
    app: FastAPI, client: TestClient, case_repository: FakeCaseRepository
) -> TestClient:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: case_repository
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_case_routes_require_a_signed_access_token(client: TestClient) -> None:
    response = client.get("/v1/cases")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_create_derives_actor_correlation_and_asset_scope_from_verified_context(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    correlation_id = str(uuid4())
    response = authenticated_client.post(
        "/v1/cases",
        headers={"Idempotency-Key": "create-vpn-1", "X-Correlation-ID": correlation_id},
        json={
            "title": "VPN tunnel unavailable",
            "category": "ipsec",
            "severity": "high",
            "asset_id": str(ASSET_ID),
            "input": {"input_kind": "syslog", "content": {"message": "IKE failed", "count": 2}},
        },
    )

    assert response.status_code == 201
    command = case_repository.created[0]
    assert command.actor.actor_id != UUID("00000000-0000-0000-0000-000000000000")
    assert command.actor.roles == frozenset({CaseRole.OPERATOR})
    assert command.correlation_id == UUID(correlation_id)
    assert command.asset_id == ASSET_ID
    assert command.case_input is not None
    assert (
        command.case_input.content_sha256
        == "950eb070231a4abf951db8fd68201a9b2bc519853341b6150243ca0a9b66248e"
    )
    assert "organization_id" not in response.json()


def test_create_rejects_unscoped_asset_without_persisting(
    app: FastAPI, client: TestClient, case_repository: FakeCaseRepository
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(asset_ids=frozenset())

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: case_repository
    try:
        response = client.post(
            "/v1/cases",
            headers={"Idempotency-Key": "unscoped-asset"},
            json={
                "title": "Secret device alert",
                "severity": "high",
                "asset_id": str(OTHER_ASSET_ID),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "asset_scope_denied"
    assert case_repository.created == []


def test_create_idempotency_replay_returns_existing_case_with_safe_signal(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.create_result_created = False

    response = authenticated_client.post(
        "/v1/cases",
        headers={"Idempotency-Key": "same-case-request"},
        json={"title": "VPN tunnel unavailable", "severity": "high"},
    )

    assert response.status_code == 200
    assert response.headers["Idempotent-Replay"] == "true"


def test_create_denies_auditors_even_when_they_have_asset_scope(
    app: FastAPI, client: TestClient, case_repository: FakeCaseRepository
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(roles=frozenset({CaseRole.AUDITOR}))

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: case_repository
    try:
        response = client.post(
            "/v1/cases",
            headers={"Idempotency-Key": "auditor-must-not-write"},
            json={"title": "VPN tunnel unavailable", "severity": "high", "asset_id": str(ASSET_ID)},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "case_write_forbidden"
    assert case_repository.created == []


def test_transition_denies_auditors_before_reading_the_case(
    app: FastAPI, client: TestClient, case_repository: FakeCaseRepository
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(roles=frozenset({CaseRole.AUDITOR}))

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: case_repository
    try:
        response = client.post(
            f"/v1/cases/{CASE_ID}/transitions",
            json={"expected_version": 0, "to_state": "investigating"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "case_write_forbidden"


def test_case_reads_deny_principals_without_a_product_role(
    app: FastAPI, client: TestClient, case_repository: FakeCaseRepository
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(roles=frozenset())

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: case_repository
    try:
        response = client.get("/v1/cases")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "case_read_forbidden"


def test_create_requires_idempotency_key_and_forbids_unknown_fields(
    authenticated_client: TestClient,
) -> None:
    missing_key = authenticated_client.post(
        "/v1/cases", json={"title": "VPN tunnel unavailable", "severity": "high"}
    )
    unknown_field = authenticated_client.post(
        "/v1/cases",
        headers={"Idempotency-Key": "strict-body"},
        json={
            "title": "VPN tunnel unavailable",
            "severity": "high",
            "organization_id": str(uuid4()),
        },
    )

    assert missing_key.status_code == 422
    assert unknown_field.status_code == 422
    assert unknown_field.json()["error"]["code"] == "validation_error"


def test_list_filters_cases_outside_signed_asset_scope(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.cases = (
        _case(asset_id=None),
        _case(asset_id=ASSET_ID),
        _case(asset_id=OTHER_ASSET_ID),
    )

    response = authenticated_client.get("/v1/cases")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert {item["asset_id"] for item in response.json()["items"]} == {None, str(ASSET_ID)}


def test_list_forwards_server_side_queue_filters_and_cursor(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    cursor = _encode_case_list_cursor(CaseListCursor(updated_at=NOW, case_id=CASE_ID))
    case_repository.cases = (_case(asset_id=None),)

    response = authenticated_client.get(
        "/v1/cases",
        params={
            "cursor": cursor,
            "limit": "25",
            "q": "VPN",
            "state": "investigating",
            "severity": "high",
        },
    )

    assert response.status_code == 200
    assert response.json()["next_cursor"] is None
    assert case_repository.list_arguments == {
        "asset_ids": (ASSET_ID,),
        "cursor": CaseListCursor(updated_at=NOW, case_id=CASE_ID),
        "limit": 25,
        "query": "VPN",
        "severity": "high",
        "state": CaseState.INVESTIGATING,
    }


def test_list_rejects_malformed_cursor_without_calling_repository(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    response = authenticated_client.get("/v1/cases", params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_case_cursor"
    assert case_repository.list_arguments is None


def test_detail_and_timeline_hide_cases_outside_signed_asset_scope(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.detail = CaseDetail(case=_case(asset_id=OTHER_ASSET_ID), timeline=())

    detail = authenticated_client.get(f"/v1/cases/{CASE_ID}")
    timeline = authenticated_client.get(f"/v1/cases/{CASE_ID}/timeline")

    assert detail.status_code == 404
    assert timeline.status_code == 404
    assert detail.json()["error"]["code"] == "case_not_found"


def test_transition_uses_compare_and_swap_and_maps_conflict_safely(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.detail = CaseDetail(case=_case(), timeline=())
    case_repository.snapshot = CaseSnapshot(CASE_ID, CaseState.NEW, 1)

    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/transitions",
        json={"expected_version": 0, "to_state": "investigating"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "case_version_conflict",
        "message": "The case changed before this transition could be applied.",
        "request_id": response.headers["X-Correlation-ID"],
        "details": None,
    }
    assert case_repository.persisted is None


def test_transition_rejects_invalid_edge_without_persisting(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.detail = CaseDetail(case=_case(), timeline=())
    case_repository.snapshot = CaseSnapshot(CASE_ID, CaseState.NEW, 0)

    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/transitions",
        json={"expected_version": 0, "to_state": "resolved", "verification_note": "checked"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_case_transition"
    assert case_repository.persisted is None


def test_transition_requires_case_asset_scope_before_state_mutation(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.detail = CaseDetail(case=_case(asset_id=OTHER_ASSET_ID), timeline=())
    case_repository.snapshot = CaseSnapshot(CASE_ID, CaseState.NEW, 0)

    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/transitions",
        json={"expected_version": 0, "to_state": "investigating"},
    )

    assert response.status_code == 404
    assert case_repository.persisted is None


def test_resolution_route_requires_verification_and_uses_resolved_transition(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.detail = CaseDetail(
        case=_case(version=4, state=CaseState.CONFIRMED), timeline=()
    )
    case_repository.snapshot = CaseSnapshot(CASE_ID, CaseState.CONFIRMED, 4)

    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/resolution",
        json={"expected_version": 4, "verification_note": "Traffic confirmed stable."},
    )

    assert response.status_code == 200
    assert case_repository.persisted is not None
    assert case_repository.persisted.transition.to_state is CaseState.RESOLVED
    assert case_repository.persisted.transition.verification_note == "Traffic confirmed stable."


def test_feedback_route_uses_needs_information_transition(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    case_repository.detail = CaseDetail(
        case=_case(version=1, state=CaseState.INVESTIGATING), timeline=()
    )
    case_repository.snapshot = CaseSnapshot(CASE_ID, CaseState.INVESTIGATING, 1)

    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/feedback",
        json={"expected_version": 1, "note": "Please attach the peer device logs."},
    )

    assert response.status_code == 200
    assert case_repository.persisted is not None
    assert case_repository.persisted.transition.to_state is CaseState.NEEDS_INFORMATION
    assert case_repository.persisted.transition.note == "Please attach the peer device logs."


def test_timeline_response_is_immutable_history_contract(
    authenticated_client: TestClient, case_repository: FakeCaseRepository
) -> None:
    entry = CaseTimelineEntry(
        event_id=uuid4(),
        event_type="case.created.v1",
        aggregate_version=0,
        transition_id=None,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        occurred_at=NOW,
        from_state=None,
        to_state=None,
        approval_id=None,
        verification_note=None,
        knowledge_item_id=None,
        note=None,
    )
    case_repository.detail = CaseDetail(case=_case(), timeline=(entry,))

    response = authenticated_client.get(f"/v1/cases/{CASE_ID}/timeline")

    assert response.status_code == 200
    assert response.json() == [
        {
            "event_id": str(entry.event_id),
            "event_type": "case.created.v1",
            "aggregate_version": 0,
            "transition_id": None,
            "actor_id": str(entry.actor_id),
            "correlation_id": str(entry.correlation_id),
            "occurred_at": "2026-07-16T00:00:00Z",
            "from_state": None,
            "to_state": None,
            "approval_id": None,
            "verification_note": None,
            "knowledge_item_id": None,
            "note": None,
        }
    ]
