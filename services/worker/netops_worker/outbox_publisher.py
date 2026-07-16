"""At-least-once transactional outbox publisher for one tenant.

The publisher commits a short database lease before invoking its transport. It
only marks a row published after that handoff returns successfully. A process
crash between those two operations therefore leaves the row unpublished; the
lease expires and the event is delivered again instead of being lost.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection

from netops_api.application.outbox import (
    LeasedOutboxEvent,
    OutboxLease,
    OutboxLeaseLostError,
    TenantOutboxRepository,
)

_RETRYABLE_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")


class OutboxTransport(Protocol):
    """Trusted downstream handoff for one immutable business event."""

    def publish(self, event: LeasedOutboxEvent) -> None:
        """Durably hand off an event or raise without acknowledging it."""


class RetryablePublishError(RuntimeError):
    """A transport failure represented by a safe, non-sensitive retry code."""

    def __init__(self, error_code: str) -> None:
        if not _RETRYABLE_ERROR_CODE.fullmatch(error_code):
            raise ValueError("error_code must be a 1 to 128 character safe delivery code.")
        super().__init__(error_code)
        self.error_code = error_code


class OutboxRepository(Protocol):
    """Persistence port needed by the publisher loop."""

    def lease_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_for: timedelta,
    ) -> tuple[LeasedOutboxEvent, ...]: ...

    def mark_published(self, lease: OutboxLease, *, published_at: datetime) -> None: ...

    def schedule_retry(
        self,
        lease: OutboxLease,
        *,
        retry_at: datetime,
        error_code: str,
    ) -> None: ...


class TenantConnectionSource(Protocol):
    """The production database boundary, kept small for deterministic tests."""

    def tenant_connection(self, organization_id: UUID) -> AbstractContextManager[Connection]: ...


RepositoryFactory = Callable[[Connection, UUID], OutboxRepository]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class OutboxPublisherSettings:
    """Bounded operational settings for a single tenant publisher instance."""

    worker_id: str
    batch_size: int = 50
    lease_for: timedelta = timedelta(minutes=1)
    retry_after: timedelta = timedelta(seconds=30)
    poll_interval: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        if not self.worker_id.strip() or len(self.worker_id) > 200:
            raise ValueError("worker_id must contain 1 to 200 non-blank characters.")
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100.")
        if self.lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive.")
        if self.retry_after <= timedelta(0):
            raise ValueError("retry_after must be positive.")
        if self.poll_interval <= timedelta(0):
            raise ValueError("poll_interval must be positive.")


@dataclass(frozen=True, slots=True)
class PublishCycleResult:
    """Safe aggregate outcome of one bounded publisher sweep."""

    leased: int = 0
    published: int = 0
    retries_scheduled: int = 0
    settlements_lost: int = 0


class OutboxPublisher:
    """Drive transactional outbox delivery without acknowledging before handoff."""

    def __init__(
        self,
        database: TenantConnectionSource,
        organization_id: UUID,
        transport: OutboxTransport,
        settings: OutboxPublisherSettings,
        *,
        repository_factory: RepositoryFactory = TenantOutboxRepository,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._organization_id = organization_id
        self._transport = transport
        self._settings = settings
        self._repository_factory = repository_factory
        self._clock = clock or _utc_now

    def publish_once(self) -> PublishCycleResult:
        """Lease, hand off, then settle at most one bounded batch.

        Leasing occurs in its own transaction, which commits before any transport
        call. Successful handoff followed by an acknowledgement crash is safe:
        the row remains unpublished and is recovered when its lease expires.
        """
        leased_at = self._now()
        with self._database.tenant_connection(self._organization_id) as connection:
            repository = self._repository_factory(connection, self._organization_id)
            events = repository.lease_available(
                worker_id=self._settings.worker_id,
                now=leased_at,
                limit=self._settings.batch_size,
                lease_for=self._settings.lease_for,
            )

        published = 0
        retries_scheduled = 0
        settlements_lost = 0
        for event in events:
            try:
                self._transport.publish(event)
            except RetryablePublishError as error:
                if self._schedule_retry(event.lease, error.error_code):
                    retries_scheduled += 1
            except Exception:
                # Unknown exceptions often contain request data or credentials.
                # Persist only this stable code, never exception text.
                if self._schedule_retry(event.lease, "transport.failed"):
                    retries_scheduled += 1
            else:
                if self._mark_published(event.lease):
                    published += 1
                else:
                    settlements_lost += 1
        return PublishCycleResult(
            leased=len(events),
            published=published,
            retries_scheduled=retries_scheduled,
            settlements_lost=settlements_lost,
        )

    async def run_until_stopped(self, stop_event: asyncio.Event) -> None:
        """Continuously run bounded sweeps without blocking Temporal polling."""
        while not stop_event.is_set():
            await asyncio.to_thread(self.publish_once)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._settings.poll_interval.total_seconds()
                )
            except TimeoutError:
                pass

    def _mark_published(self, lease: OutboxLease) -> bool:
        try:
            with self._database.tenant_connection(self._organization_id) as connection:
                self._repository_factory(connection, self._organization_id).mark_published(
                    lease, published_at=self._now()
                )
        except OutboxLeaseLostError:
            return False
        return True

    def _schedule_retry(self, lease: OutboxLease, error_code: str) -> bool:
        try:
            with self._database.tenant_connection(self._organization_id) as connection:
                self._repository_factory(connection, self._organization_id).schedule_retry(
                    lease,
                    retry_at=self._now() + self._settings.retry_after,
                    error_code=error_code,
                )
        except OutboxLeaseLostError:
            # Another worker recovered the expired lease. Do not overwrite its
            # delivery state; the event remains safe in the outbox.
            return False
        return True

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is not UTC or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("clock must return a timezone-aware UTC datetime.")
        return value


def _utc_now() -> datetime:
    return datetime.now(UTC)
