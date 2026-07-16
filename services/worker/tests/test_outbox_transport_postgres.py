"""Real PostgreSQL contract for the worker's durable receipt transport."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from netops_api.application.cases import CreateCaseCommand, TenantCaseRepository
from netops_api.application.outbox import (
    LeasedOutboxEvent,
    OutboxLease,
    OutboxLeaseLostError,
    TenantOutboxRepository,
)
from netops_api.core.database import TenantDatabase
from netops_api.domain.cases import Actor, ActorKind, CaseRole
from netops_worker.outbox_publisher import (
    OutboxPublisher,
    OutboxPublisherSettings,
    RepositoryFactory,
)
from netops_worker.outbox_transport import DurableInboxReceiptTransport
from tests.integration.test_tenant_rls import ORGANIZATION_A, _prepared_tenants, _required_url

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 16, tzinfo=UTC)
ACTOR_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c1")
CORRELATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c2")


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


def _create_case(database: TenantDatabase) -> None:
    command = CreateCaseCommand(
        case_id=uuid4(),
        event_id=uuid4(),
        idempotency_key=f"worker-receipt-{uuid4()}",
        title="Worker receipt transport contract",
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


def _publisher(
    database: TenantDatabase,
    *,
    now: datetime,
    repository_factory: RepositoryFactory = TenantOutboxRepository,
) -> OutboxPublisher:
    return OutboxPublisher(
        database,
        ORGANIZATION_A,
        DurableInboxReceiptTransport(database, ORGANIZATION_A, clock=lambda: now),
        OutboxPublisherSettings(
            worker_id="worker-receipt-contract",
            lease_for=timedelta(seconds=5),
            retry_after=timedelta(seconds=5),
        ),
        repository_factory=repository_factory,
        clock=lambda: now,
    )


class CrashAfterReceiptRepository:
    """Simulate process death after durable receipt but before outbox acknowledgement."""

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._repository = TenantOutboxRepository(connection, organization_id)

    def lease_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_for: timedelta,
    ) -> tuple[LeasedOutboxEvent, ...]:
        return self._repository.lease_available(
            worker_id=worker_id, now=now, limit=limit, lease_for=lease_for
        )

    def mark_published(self, lease: OutboxLease, *, published_at: datetime) -> None:
        raise OutboxLeaseLostError(lease.outbox_id)

    def schedule_retry(
        self,
        lease: OutboxLease,
        *,
        retry_at: datetime,
        error_code: str,
    ) -> None:
        self._repository.schedule_retry(lease, retry_at=retry_at, error_code=error_code)


def test_durable_receipt_transport_uses_rls_and_deduplicates_recovery_replay(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        _create_case(database)
        with database.tenant_connection(ORGANIZATION_A) as connection:
            event = TenantOutboxRepository(connection, ORGANIZATION_A).lease_available(
                worker_id="worker-receipt-contract", now=NOW
            )[0]

        transport = DurableInboxReceiptTransport(
            database, ORGANIZATION_A, clock=lambda: NOW
        )
        transport.publish(event)
        # This models a worker crash after durable delivery but before the
        # outbox acknowledgement: replay must remain an exact-once receipt.
        transport.publish(event)

        with database.tenant_connection(ORGANIZATION_A) as connection:
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM consumer_inbox "
                    "WHERE consumer_name = 'case-event-receipt.v1' AND event_id = :event_id"
                ),
                {"event_id": event.case_event_id},
            ) == 1


def test_outbox_publisher_delivers_durable_receipt_then_acknowledges(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        _create_case(database)
        result = _publisher(database, now=NOW).publish_once()

        assert result.leased == 1
        assert result.published == 1
        with database.tenant_connection(ORGANIZATION_A) as connection:
            assert connection.scalar(text("SELECT count(*) FROM consumer_inbox")) == 1
            assert connection.scalar(
                text("SELECT published_at IS NOT NULL FROM outbox_events")
            ) is True


def test_outbox_publisher_recovers_ack_crash_without_duplicate_durable_receipt(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        _create_case(database)
        crashed = _publisher(
            database, now=NOW, repository_factory=CrashAfterReceiptRepository
        ).publish_once()
        assert crashed.leased == 1
        assert crashed.settlements_lost == 1

        recovered = _publisher(database, now=NOW + timedelta(seconds=10)).publish_once()
        assert recovered.leased == 1
        assert recovered.published == 1
        with database.tenant_connection(ORGANIZATION_A) as connection:
            assert connection.scalar(text("SELECT count(*) FROM consumer_inbox")) == 1
            assert connection.scalar(
                text("SELECT published_at IS NOT NULL FROM outbox_events")
            ) is True
            assert connection.scalar(text("SELECT attempt_count FROM outbox_events")) == 2
