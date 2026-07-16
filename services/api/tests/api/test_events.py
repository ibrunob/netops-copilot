"""HTTP contract tests for recoverable case-event SSE replay."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.api.events import get_case_event_feed
from netops_api.application.event_feed import CaseFeedEvent, EventCursorNotFoundError
from netops_api.core.auth import AuthenticatedPrincipal
from netops_api.core.dependencies import get_current_principal
from netops_api.domain.cases import CaseRole

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677b1")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677b2")
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _principal(
    *, roles: frozenset[CaseRole] = frozenset({CaseRole.OPERATOR})
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="operator-1",
        organization_id=ORGANIZATION_ID,
        roles=roles,
        asset_ids=frozenset({ASSET_ID}),
        issuer="https://issuer.example.test/realms/netops",
        client_id="netops-web",
    )


def _event(*, event_id: UUID | None = None, sequence: int = 0) -> CaseFeedEvent:
    return CaseFeedEvent(
        event_id=event_id or uuid4(),
        case_id=uuid4(),
        event_type="case.investigating.v1",
        aggregate_version=sequence,
        transition_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        occurred_at=NOW,
    )


@dataclass
class FakeEventFeed:
    events: tuple[CaseFeedEvent, ...] = ()
    cursor_error: UUID | None = None
    arguments: tuple[UUID | None, tuple[UUID, ...], int] | None = None

    def read_after(
        self,
        *,
        after_event_id: UUID | None,
        asset_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[CaseFeedEvent, ...]:
        self.arguments = (after_event_id, asset_ids, limit)
        if self.cursor_error is not None:
            raise EventCursorNotFoundError(self.cursor_error)
        return self.events


@pytest.fixture
def feed() -> FakeEventFeed:
    return FakeEventFeed()


@pytest.fixture
def authenticated_client(app: FastAPI, client: TestClient, feed: FakeEventFeed) -> TestClient:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_event_feed] = lambda: feed
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_event_replay_requires_a_signed_access_token(client: TestClient) -> None:
    response = client.get("/v1/events")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_event_replay_is_sse_and_derives_asset_scope_from_principal(
    authenticated_client: TestClient, feed: FakeEventFeed
) -> None:
    first = _event(sequence=1)
    second = _event(sequence=2)
    feed.events = (first, second)

    response = authenticated_client.get("/v1/events?limit=2")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.splitlines()[0] == f"id: {first.event_id}"
    assert response.text.splitlines()[4] == f"id: {second.event_id}"
    payload = json.loads(response.text.splitlines()[2].removeprefix("data: "))
    assert payload["event_id"] == str(first.event_id)
    assert "organization_id" not in payload
    assert "asset_id" not in payload
    assert feed.arguments == (None, (ASSET_ID,), 2)


def test_event_replay_uses_last_event_id_as_its_persisted_recovery_cursor(
    authenticated_client: TestClient, feed: FakeEventFeed
) -> None:
    cursor = uuid4()
    response = authenticated_client.get("/v1/events", headers={"Last-Event-ID": str(cursor)})

    assert response.status_code == 200
    assert feed.arguments == (cursor, (ASSET_ID,), 100)


def test_event_replay_rejects_malformed_or_unavailable_cursors(
    authenticated_client: TestClient, feed: FakeEventFeed
) -> None:
    malformed = authenticated_client.get("/v1/events", headers={"Last-Event-ID": "not-a-uuid"})
    unavailable_id = uuid4()
    feed.cursor_error = unavailable_id
    unavailable = authenticated_client.get(
        "/v1/events", headers={"Last-Event-ID": str(unavailable_id)}
    )

    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_event_cursor"
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "event_cursor_unavailable"


def test_event_replay_denies_principals_without_case_read_role(
    app: FastAPI, client: TestClient, feed: FakeEventFeed
) -> None:
    async def principal_override() -> AuthenticatedPrincipal:
        return _principal(roles=frozenset())

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_case_event_feed] = lambda: feed
    try:
        response = client.get("/v1/events")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "case_read_forbidden"
    assert feed.arguments is None
