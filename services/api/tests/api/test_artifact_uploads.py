"""Contract tests for byte-free, case-scoped upload capabilities."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.api.artifact_uploads import get_artifact_store
from netops_api.api.cases import get_case_repository
from netops_api.application.artifacts import FakeArtifactStore
from netops_api.application.cases import CaseDetail, CaseNotFoundError, CaseRecord
from netops_api.core.auth import AuthenticatedPrincipal
from netops_api.core.dependencies import get_current_principal, get_tenant_connection
from netops_api.domain.cases import CaseRole, CaseState

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667801")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667802")
OTHER_ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667803")
CASE_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667804")
SHA256 = "a" * 64


def _principal(
    *,
    roles: frozenset[CaseRole] = frozenset({CaseRole.OPERATOR}),
    asset_ids: frozenset[UUID] = frozenset({ASSET_ID}),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="operator-1",
        organization_id=ORGANIZATION_ID,
        roles=roles,
        asset_ids=asset_ids,
        issuer="https://issuer.example.test/realms/netops",
        client_id="netops-web",
    )


def _case(asset_id: UUID | None = None) -> CaseRecord:
    now = datetime(2026, 7, 16, tzinfo=UTC)
    return CaseRecord(
        case_id=CASE_ID,
        state=CaseState.NEW,
        version=0,
        title="Router configuration needed",
        category="routing",
        severity="high",
        asset_id=asset_id,
        created_by_actor_id=uuid4(),
        created_at=now,
        updated_at=now,
    )


@dataclass
class FakeCaseRepository:
    detail: CaseDetail | None = None
    detail_calls: int = 0

    def get_detail(self, case_id: UUID) -> CaseDetail:
        self.detail_calls += 1
        if self.detail is None or self.detail.case.case_id != case_id:
            raise CaseNotFoundError(case_id)
        return self.detail


@dataclass
class FakeConnection:
    statements: list[tuple[object, dict[str, object]]] = field(default_factory=list)

    @contextmanager
    def begin_nested(self) -> Iterator[None]:
        yield

    def execute(self, statement: object, parameters: dict[str, object]) -> None:
        self.statements.append((statement, parameters))


@pytest.fixture
def repository() -> FakeCaseRepository:
    return FakeCaseRepository(detail=CaseDetail(case=_case(), timeline=()))


@pytest.fixture
def authenticated_client(
    app: FastAPI, client: TestClient, repository: FakeCaseRepository
) -> tuple[TestClient, FakeArtifactStore, FakeConnection]:
    store = FakeArtifactStore()
    connection = FakeConnection()

    async def principal_override() -> AuthenticatedPrincipal:
        return _principal()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    app.dependency_overrides[get_tenant_connection] = lambda: connection
    app.dependency_overrides[get_artifact_store] = lambda: store
    try:
        yield client, store, connection
    finally:
        app.dependency_overrides.clear()


def _body(**overrides: object) -> dict[str, object]:
    return {
        "artifact_kind": "network-configuration",
        "content_type": "text/plain",
        "content_length": 32,
        "sha256": SHA256,
        "original_filename": "edge-router.conf",
        **overrides,
    }


def test_creates_case_scoped_byte_free_upload_capability_and_audits_it(
    authenticated_client: tuple[TestClient, FakeArtifactStore, FakeConnection],
) -> None:
    client, store, connection = authenticated_client

    response = client.post(f"/v1/cases/{CASE_ID}/artifacts/upload-intents", json=_body())

    assert response.status_code == 200
    response_body = response.json()
    assert set(response_body) == {
        "artifact_id",
        "intent_id",
        "upload_url",
        "required_headers",
        "expires_at",
    }
    assert "storage_key" not in response_body
    assert response_body["required_headers"] == {
        "content-type": "text/plain",
        "x-amz-meta-sha256": SHA256,
    }
    assert len(store.requests) == 1
    assert store.requests[0].case_id == CASE_ID
    assert store.requests[0].organization_id == ORGANIZATION_ID
    assert len(connection.statements) == 2
    persisted = connection.statements[0][1]
    assert persisted["original_filename"] == "edge-router.conf"
    assert persisted["sha256"] == SHA256
    assert "edge-router.conf" not in response.text
    audit = connection.statements[1][1]
    assert '"intent_id"' in str(audit["details"])
    assert "edge-router.conf" not in str(audit["details"])


def test_rejects_unpermitted_metadata_before_storage(
    authenticated_client: tuple[TestClient, FakeArtifactStore, FakeConnection],
) -> None:
    client, store, connection = authenticated_client

    response = client.post(
        f"/v1/cases/{CASE_ID}/artifacts/upload-intents",
        json=_body(content_type="application/pdf", original_filename="../../secret"),
    )

    assert response.status_code == 422
    assert store.requests == []
    assert connection.statements == []


def test_hides_case_outside_signed_asset_scope(
    authenticated_client: tuple[TestClient, FakeArtifactStore, FakeConnection],
    repository: FakeCaseRepository,
) -> None:
    client, store, connection = authenticated_client
    repository.detail = CaseDetail(case=_case(OTHER_ASSET_ID), timeline=())

    response = client.post(f"/v1/cases/{CASE_ID}/artifacts/upload-intents", json=_body())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"
    assert store.requests == []
    assert connection.statements == []


def test_read_only_role_is_denied_before_case_or_storage(
    app: FastAPI, client: TestClient, repository: FakeCaseRepository
) -> None:
    store = FakeArtifactStore()
    connection = FakeConnection()

    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(roles=frozenset({CaseRole.AUDITOR}))

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    app.dependency_overrides[get_tenant_connection] = lambda: connection
    app.dependency_overrides[get_artifact_store] = lambda: store
    try:
        response = client.post(f"/v1/cases/{CASE_ID}/artifacts/upload-intents", json=_body())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert repository.detail_calls == 0
    assert store.requests == []
    assert connection.statements == []
