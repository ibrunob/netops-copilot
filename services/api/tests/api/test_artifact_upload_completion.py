"""HTTP contract for HEAD-only artifact upload completion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.api.artifact_uploads import get_artifact_store
from netops_api.api.cases import get_case_repository
from netops_api.application.artifact_intents import (
    ArtifactUploadIntentExpiredError,
    CompletedArtifactUpload,
    TenantArtifactIntentRepository,
)
from netops_api.application.artifacts import FakeArtifactStore
from netops_api.application.cases import CaseDetail, CaseNotFoundError, CaseRecord
from netops_api.core.auth import AuthenticatedPrincipal
from netops_api.core.dependencies import get_current_principal, get_tenant_connection
from netops_api.domain.cases import CaseRole, CaseState

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667901")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667902")
CASE_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667903")
INTENT_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667904")
ARTIFACT_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667905")


def _principal(
    roles: frozenset[CaseRole] = frozenset({CaseRole.OPERATOR}),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="operator-1",
        organization_id=ORGANIZATION_ID,
        roles=roles,
        asset_ids=frozenset({ASSET_ID}),
        issuer="https://issuer.example.test/realms/netops",
        client_id="netops-web",
    )


@dataclass
class FakeCaseRepository:
    calls: int = 0

    def get_detail(self, case_id: UUID) -> CaseDetail:
        self.calls += 1
        if case_id != CASE_ID:
            raise CaseNotFoundError(case_id)
        now = datetime(2026, 7, 16, tzinfo=UTC)
        return CaseDetail(
            case=CaseRecord(
                case_id=CASE_ID,
                state=CaseState.NEW,
                version=0,
                title="Router config",
                category=None,
                severity="high",
                asset_id=ASSET_ID,
                created_by_actor_id=uuid4(),
                created_at=now,
                updated_at=now,
            ),
            timeline=(),
        )


def _configure(
    app: FastAPI, repository: FakeCaseRepository, principal: AuthenticatedPrincipal
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return principal

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    app.dependency_overrides[get_tenant_connection] = lambda: object()
    app.dependency_overrides[get_artifact_store] = lambda: FakeArtifactStore()


def test_completion_returns_only_safe_result_and_passes_scoped_dependencies(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FakeCaseRepository()
    _configure(app, repository, _principal())
    seen: dict[str, object] = {}
    completed_at = datetime(2026, 7, 16, tzinfo=UTC)

    def complete(self: TenantArtifactIntentRepository, **kwargs: object) -> CompletedArtifactUpload:
        seen.update(kwargs)
        return CompletedArtifactUpload(ARTIFACT_ID, completed_at, False)

    monkeypatch.setattr(TenantArtifactIntentRepository, "complete", complete)
    try:
        response = client.post(f"/v1/cases/{CASE_ID}/artifacts/upload-intents/{INTENT_ID}/complete")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "artifact_id": str(ARTIFACT_ID),
        "completed_at": "2026-07-16T00:00:00Z",
        "already_completed": False,
    }
    assert seen["case_id"] == CASE_ID
    assert seen["intent_id"] == INTENT_ID
    assert "storage_key" not in response.text


def test_completion_denies_auditor_before_case_or_store(app: FastAPI, client: TestClient) -> None:
    repository = FakeCaseRepository()
    _configure(app, repository, _principal(frozenset({CaseRole.AUDITOR})))
    try:
        response = client.post(f"/v1/cases/{CASE_ID}/artifacts/upload-intents/{INTENT_ID}/complete")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "case_write_forbidden"
    assert repository.calls == 0


def test_completion_maps_expired_intent_without_disclosing_metadata(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FakeCaseRepository()
    _configure(app, repository, _principal())

    def complete(self: TenantArtifactIntentRepository, **kwargs: object) -> CompletedArtifactUpload:
        raise ArtifactUploadIntentExpiredError("secret object details")

    monkeypatch.setattr(TenantArtifactIntentRepository, "complete", complete)
    try:
        response = client.post(f"/v1/cases/{CASE_ID}/artifacts/upload-intents/{INTENT_ID}/complete")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "artifact_upload_intent_expired"
    assert "secret object details" not in response.text
