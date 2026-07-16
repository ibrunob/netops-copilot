from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from netops_api.application.outbox import LeasedOutboxEvent, OutboxLease, OutboxLeaseLostError
from netops_worker.outbox_publisher import (
    OutboxPublisher,
    OutboxPublisherSettings,
    RetryablePublishError,
)

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
OUTBOX_ID = UUID("00000000-0000-0000-0000-000000000002")
CASE_ID = UUID("00000000-0000-0000-0000-000000000003")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000004")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000005")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


class FakeDatabase:
    def tenant_connection(self, _: UUID) -> Any:
        return nullcontext(object())


class FakeRepository:
    def __init__(self, events: tuple[LeasedOutboxEvent, ...]) -> None:
        self.events = events
        self.lease_calls: list[dict[str, object]] = []
        self.published: list[tuple[OutboxLease, datetime]] = []
        self.retries: list[tuple[OutboxLease, datetime, str]] = []
        self.lose_publish = False
        self.lose_retry = False

    def lease_available(self, **kwargs: object) -> tuple[LeasedOutboxEvent, ...]:
        self.lease_calls.append(kwargs)
        return self.events

    def mark_published(self, lease: OutboxLease, *, published_at: datetime) -> None:
        if self.lose_publish:
            raise OutboxLeaseLostError(lease.outbox_id)
        self.published.append((lease, published_at))

    def schedule_retry(
        self, lease: OutboxLease, *, retry_at: datetime, error_code: str
    ) -> None:
        if self.lose_retry:
            raise OutboxLeaseLostError(lease.outbox_id)
        self.retries.append((lease, retry_at, error_code))


class RecordingTransport:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.events: list[LeasedOutboxEvent] = []

    def publish(self, event: LeasedOutboxEvent) -> None:
        self.events.append(event)
        if self.failure is not None:
            raise self.failure


def event() -> LeasedOutboxEvent:
    return LeasedOutboxEvent(
        outbox_id=OUTBOX_ID,
        organization_id=ORGANIZATION_ID,
        case_id=CASE_ID,
        case_event_id=EVENT_ID,
        event_type="case.created.v1",
        aggregate_version=0,
        correlation_id=CORRELATION_ID,
        payload={"case_id": str(CASE_ID)},
        available_at=NOW,
        created_at=NOW,
        attempt_count=1,
        locked_at=NOW,
        locked_by="publisher-a:opaque-token",
    )


def publisher(repository: FakeRepository, transport: RecordingTransport) -> OutboxPublisher:
    return OutboxPublisher(
        FakeDatabase(),  # type: ignore[arg-type]
        ORGANIZATION_ID,
        transport,
        OutboxPublisherSettings(
            worker_id="publisher-a",
            retry_after=timedelta(seconds=20),
            poll_interval=timedelta(milliseconds=1),
        ),
        repository_factory=lambda *_: repository,
        clock=lambda: NOW,
    )


def test_publisher_acks_only_after_successful_transport_handoff() -> None:
    repository = FakeRepository((event(),))
    transport = RecordingTransport()

    result = publisher(repository, transport).publish_once()

    assert result.published == 1
    assert result.retries_scheduled == 0
    assert transport.events == [event()]
    assert repository.published == [(event().lease, NOW)]
    assert repository.retries == []


def test_publisher_retries_transport_failures_with_safe_codes_only() -> None:
    repository = FakeRepository((event(),))
    transport = RecordingTransport(RetryablePublishError("broker.unavailable"))

    result = publisher(repository, transport).publish_once()

    assert result.published == 0
    assert result.retries_scheduled == 1
    assert repository.published == []
    assert repository.retries == [
        (event().lease, NOW + timedelta(seconds=20), "broker.unavailable")
    ]


def test_publisher_does_not_lose_event_when_acknowledgement_lease_is_gone() -> None:
    repository = FakeRepository((event(),))
    repository.lose_publish = True
    transport = RecordingTransport()

    result = publisher(repository, transport).publish_once()

    assert transport.events == [event()]
    assert result.published == 0
    assert result.retries_scheduled == 0
    assert result.settlements_lost == 1
    assert repository.retries == []


def test_publisher_hides_unexpected_exception_text_behind_generic_retry_code() -> None:
    repository = FakeRepository((event(),))
    transport = RecordingTransport(RuntimeError("token=secret payload=unredacted"))

    result = publisher(repository, transport).publish_once()

    assert result.retries_scheduled == 1
    assert repository.retries[0][2] == "transport.failed"


def test_retryable_transport_error_rejects_unsafe_exception_text() -> None:
    with pytest.raises(ValueError, match="error_code"):
        RetryablePublishError("broker response payload={secret}")


def test_publisher_loop_stops_without_starting_another_sweep() -> None:
    repository = FakeRepository(())
    stop_event = asyncio.Event()
    stop_event.set()

    asyncio.run(publisher(repository, RecordingTransport()).run_until_stopped(stop_event))

    assert repository.lease_calls == []


@pytest.mark.parametrize("worker_id", ["", " ", "x" * 201])
def test_publisher_settings_reject_invalid_worker_id(worker_id: str) -> None:
    with pytest.raises(ValueError, match="worker_id"):
        OutboxPublisherSettings(worker_id=worker_id)
