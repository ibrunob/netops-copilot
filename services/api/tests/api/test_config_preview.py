"""HTTP contract for safe, non-persistent Cisco config previews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.api.cases import get_case_repository
from netops_api.application.cases import CaseDetail, CaseNotFoundError, CaseRecord
from netops_api.application.config_preview import CONFIG_PREVIEW_MAX_BYTES, CONFIG_PREVIEW_MAX_LINES
from netops_api.core.auth import AuthenticatedPrincipal
from netops_api.core.dependencies import get_current_principal
from netops_api.domain.cases import CaseRole, CaseState

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667701")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667702")
OTHER_ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667703")
CASE_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667704")


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
        title="VPN tunnel unavailable",
        category="ipsec",
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


@pytest.fixture
def repository() -> FakeCaseRepository:
    return FakeCaseRepository(detail=CaseDetail(case=_case(), timeline=()))


@pytest.fixture
def authenticated_client(
    app: FastAPI, client: TestClient, repository: FakeCaseRepository
) -> TestClient:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_preview_returns_only_redacted_derivative_and_count_safe_report(
    authenticated_client: TestClient,
) -> None:
    secret = "super-secret-value"
    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/config-preview",
        json={
            "config": f"username netops secret {secret}\nsnmp-server community private RO\n",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert secret not in response.text
    assert body["redacted_content"] == (
        "username netops secret <redacted:cisco.username_secret>\n"
        "snmp-server community <redacted:cisco.snmp_community> RO\n"
    )
    assert body["redaction_version"] == "cisco-redaction-v1"
    assert len(body["redacted_content_sha256"]) == 64
    assert body["report"] == {
        "source_line_count": 2,
        "redacted_line_count": 2,
        "rules": [
            {"rule_id": "cisco.snmp_community", "line_count": 1, "occurrence_count": 1},
            {"rule_id": "cisco.username_secret", "line_count": 1, "occurrence_count": 1},
        ],
    }
    assert "config" not in body


def test_preview_requires_signed_principal(client: TestClient) -> None:
    response = client.post(
        f"/v1/cases/{CASE_ID}/config-preview", json={"config": "password secret"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_preview_denies_read_only_role_before_loading_case(
    app: FastAPI, client: TestClient, repository: FakeCaseRepository
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(roles=frozenset({CaseRole.AUDITOR}))

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_repository] = lambda: repository
    try:
        response = client.post(
            f"/v1/cases/{CASE_ID}/config-preview", json={"config": "password secret"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "case_write_forbidden"
    assert repository.detail_calls == 0


def test_preview_hides_case_outside_asset_scope(
    authenticated_client: TestClient, repository: FakeCaseRepository
) -> None:
    repository.detail = CaseDetail(case=_case(OTHER_ASSET_ID), timeline=())

    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/config-preview", json={"config": "password secret"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"


@pytest.mark.parametrize(
    ("config", "expected_limit"),
    [
        ("x" * (CONFIG_PREVIEW_MAX_BYTES + 1), "bytes"),
        ("x\n" * CONFIG_PREVIEW_MAX_LINES + "x\n", "lines"),
    ],
)
def test_preview_rejects_oversized_raw_input_without_reflecting_it(
    authenticated_client: TestClient, config: str, expected_limit: str
) -> None:
    response = authenticated_client.post(
        f"/v1/cases/{CASE_ID}/config-preview", json={"config": config}
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "config_preview_limit_exceeded"
    assert expected_limit in response.json()["error"]["message"]
    assert config[:100] not in response.text
