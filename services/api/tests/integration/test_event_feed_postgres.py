"""Real PostgreSQL contracts for persisted, recoverable case-event replay."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text

from netops_api.application.cases import CreateCaseCommand, TenantCaseRepository
from netops_api.application.event_feed import EventCursorNotFoundError, TenantCaseEventFeed
from netops_api.core.database import TenantDatabase
from netops_api.domain.cases import Actor, ActorKind, CaseRole

from .test_tenant_rls import (
    ASSET_A,
    ORGANIZATION_A,
    ORGANIZATION_B,
    _prepared_tenants,
    _required_url,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(_required_url("NETOPS_RLS_OWNER_DATABASE_URL"))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def application_engine() -> Iterator[Engine]:
    engine = create_engine(_required_url("NETOPS_RLS_TEST_DATABASE_URL"))
    try:
        yield engine
    finally:
        engine.dispose()


def _create_persisted_case(
    database: TenantDatabase,
    *,
    organization_id: UUID,
    asset_id: UUID | None,
    occurred_at: datetime,
) -> CreateCaseCommand:
    command = CreateCaseCommand(
        case_id=uuid4(),
        event_id=uuid4(),
        idempotency_key=f"event-feed-{uuid4()}",
        title="Persisted event feed contract",
        category="ipsec",
        severity="high",
        asset_id=asset_id,
        actor=Actor(uuid4(), ActorKind.HUMAN, frozenset({CaseRole.OPERATOR})),
        correlation_id=uuid4(),
        occurred_at=occurred_at,
    )
    with database.tenant_connection(organization_id) as connection:
        result = TenantCaseRepository(connection, organization_id).create_case(command)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.created is True
    return command


def test_feed_replays_persisted_events_in_order_and_recovers_after_sse_cursor(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        first = _create_persisted_case(
            database,
            organization_id=ORGANIZATION_A,
            asset_id=None,
            occurred_at=NOW,
        )
        with database.tenant_connection(ORGANIZATION_A) as connection:
            connection.execute(
                text(
                    "INSERT INTO assets (id, organization_id, name) "
                    "VALUES (:id, :organization_id, 'event-feed-edge')"
                ),
                {"id": ASSET_A, "organization_id": ORGANIZATION_A},
            )
        second = _create_persisted_case(
            database,
            organization_id=ORGANIZATION_A,
            asset_id=ASSET_A,
            occurred_at=NOW + timedelta(seconds=1),
        )
        _create_persisted_case(
            database,
            organization_id=ORGANIZATION_B,
            asset_id=None,
            occurred_at=NOW + timedelta(seconds=2),
        )

        with database.tenant_connection(ORGANIZATION_A) as connection:
            feed = TenantCaseEventFeed(connection, ORGANIZATION_A)
            replay = feed.read_after(after_event_id=None, asset_ids=(ASSET_A,), limit=10)
            recovered = feed.read_after(
                after_event_id=first.event_id,
                asset_ids=(ASSET_A,),
                limit=10,
            )

        assert [event.event_id for event in replay] == [first.event_id, second.event_id]
        assert [event.case_id for event in replay] == [first.case_id, second.case_id]
        assert [event.event_id for event in recovered] == [second.event_id]


def test_feed_hides_asset_bound_and_cross_tenant_recovery_cursors(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        with database.tenant_connection(ORGANIZATION_A) as connection:
            connection.execute(
                text(
                    "INSERT INTO assets (id, organization_id, name) "
                    "VALUES (:id, :organization_id, 'event-feed-edge')"
                ),
                {"id": ASSET_A, "organization_id": ORGANIZATION_A},
            )
        asset_case = _create_persisted_case(
            database,
            organization_id=ORGANIZATION_A,
            asset_id=ASSET_A,
            occurred_at=NOW,
        )
        tenant_a_case = _create_persisted_case(
            database,
            organization_id=ORGANIZATION_A,
            asset_id=None,
            occurred_at=NOW + timedelta(seconds=1),
        )

        with database.tenant_connection(ORGANIZATION_A) as connection:
            feed = TenantCaseEventFeed(connection, ORGANIZATION_A)
            assert [
                event.event_id
                for event in feed.read_after(after_event_id=None, asset_ids=(), limit=10)
            ] == [tenant_a_case.event_id]
            with pytest.raises(EventCursorNotFoundError):
                feed.read_after(after_event_id=asset_case.event_id, asset_ids=(), limit=10)

        with database.tenant_connection(ORGANIZATION_B) as connection:
            feed = TenantCaseEventFeed(connection, ORGANIZATION_B)
            assert feed.read_after(after_event_id=None, asset_ids=(), limit=10) == ()
            with pytest.raises(EventCursorNotFoundError):
                feed.read_after(after_event_id=tenant_a_case.event_id, asset_ids=(), limit=10)
