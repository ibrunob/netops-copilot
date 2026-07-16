"""Real PostgreSQL delivery contracts for the transactional outbox and inbox."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from netops_api.application.cases import CreateCaseCommand, TenantCaseRepository
from netops_api.application.outbox import (
    ConsumerPayloadMismatchError,
    OutboxLeaseLostError,
    TenantConsumerInbox,
    TenantOutboxRepository,
)
from netops_api.core.database import TenantDatabase
from netops_api.domain.cases import Actor, ActorKind, CaseRole

from .test_tenant_rls import ORGANIZATION_A, _prepared_tenants, _required_url

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 16, tzinfo=UTC)
ACTOR_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677b1")
CORRELATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677b2")


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


def _create_case_with_outbox(database: TenantDatabase) -> UUID:
    command = CreateCaseCommand(
        case_id=uuid4(),
        event_id=uuid4(),
        idempotency_key=f"outbox-contract-{uuid4()}",
        title="IPsec tunnel delivery contract",
        category="ipsec",
        severity="high",
        asset_id=None,
        actor=Actor(ACTOR_ID, ActorKind.HUMAN, frozenset({CaseRole.OPERATOR})),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
    )
    with database.tenant_connection(ORGANIZATION_A) as connection:
        TenantCaseRepository(connection, ORGANIZATION_A).create_case(command)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return command.case_id


def test_competing_workers_skip_locked_then_expired_lease_is_reclaimed(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        _create_case_with_outbox(database)
        first_lease_acquired = Event()
        allow_first_commit = Event()

        def first_worker() -> tuple[UUID, str, int]:
            with database.tenant_connection(ORGANIZATION_A) as connection:
                leased = TenantOutboxRepository(connection, ORGANIZATION_A).lease_available(
                    worker_id="publisher-one", now=NOW, lease_for=timedelta(seconds=30)
                )
                assert len(leased) == 1
                first_lease_acquired.set()
                assert allow_first_commit.wait(timeout=10)
                event = leased[0]
                return event.outbox_id, event.locked_by, event.attempt_count

        def competing_worker() -> tuple[UUID, ...]:
            assert first_lease_acquired.wait(timeout=10)
            with database.tenant_connection(ORGANIZATION_A) as connection:
                leased = TenantOutboxRepository(connection, ORGANIZATION_A).lease_available(
                    worker_id="publisher-two", now=NOW, lease_for=timedelta(seconds=30)
                )
            allow_first_commit.set()
            return tuple(event.outbox_id for event in leased)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(first_worker)
            competing = executor.submit(competing_worker)
            competing_result = competing.result(timeout=20)
            first_outbox_id, first_token, first_attempt_count = first.result(timeout=20)

        assert competing_result == ()
        assert first_attempt_count == 1

        with database.tenant_connection(ORGANIZATION_A) as connection:
            reclaimed = TenantOutboxRepository(connection, ORGANIZATION_A).lease_available(
                worker_id="publisher-three",
                now=NOW + timedelta(minutes=1),
                lease_for=timedelta(seconds=30),
            )
            assert len(reclaimed) == 1
            assert reclaimed[0].outbox_id == first_outbox_id
            assert reclaimed[0].attempt_count == 2
            assert reclaimed[0].locked_by != first_token


def test_stale_lease_cannot_settle_and_delivery_metadata_remains_mutable(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        _create_case_with_outbox(database)
        with database.tenant_connection(ORGANIZATION_A) as connection:
            repository = TenantOutboxRepository(connection, ORGANIZATION_A)
            old_lease = repository.lease_available(worker_id="publisher-one", now=NOW)[0].lease

        with database.tenant_connection(ORGANIZATION_A) as connection:
            repository = TenantOutboxRepository(connection, ORGANIZATION_A)
            current = repository.lease_available(
                worker_id="publisher-two",
                now=NOW + timedelta(minutes=2),
                lease_for=timedelta(seconds=30),
            )[0]
            with pytest.raises(OutboxLeaseLostError):
                repository.mark_published(old_lease, published_at=NOW + timedelta(minutes=2))
            repository.mark_published(current.lease, published_at=NOW + timedelta(minutes=2))
            assert (
                connection.scalar(
                    text("SELECT published_at IS NOT NULL FROM outbox_events WHERE id = :id"),
                    {"id": current.outbox_id},
                )
                is True
            )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE outbox_events SET payload = CAST(:payload AS jsonb) WHERE id = :id"
                    ),
                    {"id": current.outbox_id, "payload": '{"forged":true}'},
                )


def test_consumer_inbox_deduplicates_competing_delivery_and_rejects_payload_change(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)
    event_id = uuid4()
    payload = {"case_id": "same-event", "version": 1}

    with _prepared_tenants(owner_engine):

        def consume() -> Literal["new", "replayed"]:
            with database.tenant_connection(ORGANIZATION_A) as connection:
                inserted = TenantConsumerInbox(connection, ORGANIZATION_A).record_once(
                    consumer_name="sse-projector",
                    event_id=event_id,
                    payload=payload,
                    processed_at=NOW,
                )
            return "new" if inserted else "replayed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(consume), executor.submit(consume))
            outcomes = [future.result(timeout=20) for future in futures]
        assert sorted(outcomes) == ["new", "replayed"]

        with database.tenant_connection(ORGANIZATION_A) as connection:
            inbox = TenantConsumerInbox(connection, ORGANIZATION_A)
            assert not inbox.record_once(
                consumer_name="sse-projector",
                event_id=event_id,
                payload={"version": 1, "case_id": "same-event"},
                processed_at=NOW,
            )
            with pytest.raises(ConsumerPayloadMismatchError):
                inbox.record_once(
                    consumer_name="sse-projector",
                    event_id=event_id,
                    payload={"case_id": "same-event", "version": 2},
                    processed_at=NOW,
                )
