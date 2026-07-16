"""Concrete durable transport for the worker's registered outbox receipt consumer."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection

from netops_api.application.outbox import LeasedOutboxEvent, TenantConsumerInbox

Clock = Callable[[], datetime]


class TenantConnectionSource(Protocol):
    """Minimal RLS-safe database boundary used by the concrete transport."""

    def tenant_connection(self, organization_id: UUID) -> AbstractContextManager[Connection]: ...


class ConsumerInbox(Protocol):
    """Immutable receipt store that provides atomic event deduplication."""

    def record_once(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        payload: object,
        processed_at: datetime,
    ) -> bool: ...


InboxFactory = Callable[[Connection, UUID], ConsumerInbox]


class DurableInboxReceiptTransport:
    """Durably register one exact-once receipt before the outbox is acknowledged.

    ``case-event-receipt.v1`` is a real registered operational consumer: its
    immutable receipt records that this worker accepted a case event for local
    delivery.  Replays are intentionally successful but do not create another
    receipt.  That closes the crash window between downstream acceptance and
    outbox acknowledgement without weakening tenant RLS.
    """

    def __init__(
        self,
        database: TenantConnectionSource,
        organization_id: UUID,
        *,
        consumer_name: str = "case-event-receipt.v1",
        inbox_factory: InboxFactory = TenantConsumerInbox,
        clock: Clock | None = None,
    ) -> None:
        if not consumer_name.strip() or len(consumer_name) > 255:
            raise ValueError("consumer_name must contain 1 to 255 non-blank characters.")
        self._database = database
        self._organization_id = organization_id
        self._consumer_name = consumer_name.strip()
        self._inbox_factory = inbox_factory
        self._clock = clock or _utc_now

    def publish(self, event: LeasedOutboxEvent) -> None:
        """Commit a tenant-scoped durable receipt or fail without an outbox ack."""
        if event.organization_id != self._organization_id:
            raise ValueError("Outbox event organization does not match this publisher scope.")
        processed_at = self._clock()
        if (
            processed_at.tzinfo is not UTC
            or processed_at.utcoffset() != UTC.utcoffset(processed_at)
        ):
            raise ValueError("clock must return a timezone-aware UTC datetime.")
        with self._database.tenant_connection(self._organization_id) as connection:
            self._inbox_factory(connection, self._organization_id).record_once(
                consumer_name=self._consumer_name,
                event_id=event.case_event_id,
                payload=event.payload,
                processed_at=processed_at,
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)
