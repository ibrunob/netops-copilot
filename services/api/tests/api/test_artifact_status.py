"""Contract tests for the metadata-free artifact lifecycle read model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.api.cases import get_case_repository
from netops_api.application.cases import CaseDetail, CaseNotFoundError, CaseRecord
from netops_api.core.auth import AuthenticatedPrincipal
from netops_api.core.dependencies import get_current_principal, get_tenant_connection
from netops_api.domain.cases import CaseRole, CaseState

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667801")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667802")
OTHER_ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667803")
CASE_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667804")
ARTIFACT_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667805")


def _principal(asset_ids: frozenset[UUID] = frozenset({ASSET_ID})) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="auditor-1",
        organization_id=ORGANIZATION_ID,
        roles=frozenset({CaseRole.AUDITOR}),
        asset_ids=asset_ids,
        issuer="https://issuer.example.test/realms/netops",
        client_id="netops-web",
    )


def _case(asset_id: UUID | None = ASSET_ID) -> CaseRecord:
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

    def get_detail(self, case_id: UUID) -> CaseDetail:
        if self.detail is None or self.detail.case.case_id != case_id:
            raise CaseNotFoundError(case_id)
        return self.detail


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


@dataclass
class FakeConnection:
    rows: list[dict[str, object]]
    calls: list[tuple[object, dict[str, object]]] = field(default_factory=list)

    def execute(self, statement: object, parameters: dict[str, object]) -> FakeResult:
        self.calls.append((statement, parameters))
        return FakeResult(self.rows)


@pytest.fixture
def repository() -> FakeCaseRepository:
    return FakeCaseRepository(detail=CaseDetail(case=_case(), timeline=()))


def test_returns_lifecycle_states_without_artifact_metadata(
    app: FastAPI, client: TestClient, repository: FakeCaseRepository
) -> None:
    connection = FakeConnection(
        rows=[
            {
                "artifact_id": ARTIFACT_ID,
                "artifact_kind": "network-configuration",
                "status": "verified_awaiting_processing",
                "status_updated_at": datetime(2026, 7, 16, tzinfo=UTC),
                # A realistic database row may contain more fields; the projection must ignore them.
                "storage_key": "organizations/secret/cases/secret/artifacts/secret",
                "sha256": "a" * 64,
            }
        ]
    )

    async def principal_override() -> AuthenticatedPrincipal:
        return _principal()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    app.dependency_overrides[get_tenant_connection] = lambda: connection
    try:
        response = client.get(f"/v1/cases/{CASE_ID}/artifacts/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "artifact_id": str(ARTIFACT_ID),
                "artifact_kind": "network-configuration",
                "status": "verified_awaiting_processing",
                "status_updated_at": "2026-07-16T00:00:00Z",
            }
        ]
    }
    assert "storage_key" not in response.text
    assert "sha256" not in response.text
    assert connection.calls[0][1] == {"organization_id": ORGANIZATION_ID, "case_id": CASE_ID}


def test_hides_status_for_case_outside_signed_asset_scope(
    app: FastAPI, client: TestClient, repository: FakeCaseRepository
) -> None:
    repository.detail = CaseDetail(case=_case(OTHER_ASSET_ID), timeline=())
    connection = FakeConnection(rows=[])

    async def principal_override() -> AuthenticatedPrincipal:
        return _principal()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    app.dependency_overrides[get_tenant_connection] = lambda: connection
    try:
        response = client.get(f"/v1/cases/{CASE_ID}/artifacts/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"
    assert connection.calls == []
