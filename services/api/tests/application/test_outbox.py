from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from netops_api.application.outbox import (
    ConsumerPayloadMismatchError,
    OutboxLease,
    OutboxLeaseLostError,
    TenantConsumerInbox,
    TenantOutboxRepository,
)

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
OUTBOX_ID = UUID("00000000-0000-0000-0000-000000000002")
CASE_ID = UUID("00000000-0000-0000-0000-000000000003")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000004")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000005")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


class FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, rowcount: int = 1) -> None:
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._rows)


class FakeConnection:
    def __init__(self, results: list[FakeResult]) -> None:
        self._results = iter(results)
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
        self.executed.append((str(statement), parameters))
        return next(self._results)


def leased_row() -> dict[str, Any]:
    return {
        "id": OUTBOX_ID,
        "organization_id": ORGANIZATION_ID,
        "case_id": CASE_ID,
        "case_event_id": EVENT_ID,
        "event_type": "case.created.v1",
        "aggregate_version": 0,
        "correlation_id": CORRELATION_ID,
        "payload": {"case_id": str(CASE_ID)},
        "available_at": NOW,
        "created_at": NOW,
        "attempt_count": 2,
        "locked_at": NOW,
        "locked_by": "publisher-a:opaque-attempt-token",
    }


def test_lease_available_uses_skip_locked_reclaims_expired_lease_and_increments_attempt() -> None:
    connection = FakeConnection([FakeResult([leased_row()])])
    repository = TenantOutboxRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    leased = repository.lease_available(
        worker_id="publisher-a", now=NOW, limit=2, lease_for=timedelta(seconds=30)
    )

    assert leased[0].attempt_count == 2
    assert leased[0].payload == {"case_id": str(CASE_ID)}
    statement, parameters = connection.executed[0]
    assert "FOR UPDATE SKIP LOCKED" in statement
    assert "attempt_count = event.attempt_count + 1" in statement
    assert parameters["organization_id"] == ORGANIZATION_ID
    assert parameters["lease_token"].startswith("publisher-a:")
    assert parameters["lease_expired_at"] == NOW - timedelta(seconds=30)


def test_delivery_settlement_is_scoped_to_the_exact_worker_lease() -> None:
    connection = FakeConnection([FakeResult(rowcount=1), FakeResult(rowcount=0)])
    repository = TenantOutboxRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]
    lease = OutboxLease(OUTBOX_ID, "publisher-a:opaque-attempt-token")

    repository.mark_published(lease, published_at=NOW)
    with pytest.raises(OutboxLeaseLostError):
        repository.schedule_retry(
            lease, retry_at=NOW + timedelta(minutes=1), error_code="broker.unavailable"
        )

    published_statement, published_parameters = connection.executed[0]
    assert "published_at IS NULL" in published_statement
    assert "locked_by = :lease_token" in published_statement
    assert published_parameters["lease_token"] == "publisher-a:opaque-attempt-token"
    retry_statement, retry_parameters = connection.executed[1]
    assert "locked_at = NULL" in retry_statement
    assert retry_parameters["retry_at"] == NOW + timedelta(minutes=1)


def test_consumer_inbox_returns_false_for_an_existing_deduplication_key() -> None:
    expected_hash = "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    connection = FakeConnection(
        [
            FakeResult([{"id": UUID(int=8)}]),
            FakeResult(),
            FakeResult([{"payload_sha256": expected_hash}]),
        ]
    )
    inbox = TenantConsumerInbox(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    assert inbox.record_once(
        consumer_name="sse-projector", event_id=EVENT_ID, payload={"b": 2, "a": 1}, processed_at=NOW
    )
    assert not inbox.record_once(
        consumer_name="sse-projector", event_id=EVENT_ID, payload={"a": 1, "b": 2}, processed_at=NOW
    )

    statement, parameters = connection.executed[0]
    assert "ON CONFLICT (organization_id, consumer_name, event_id) DO NOTHING" in statement
    assert parameters["payload_sha256"] == connection.executed[1][1]["payload_sha256"]
    assert parameters["payload_sha256"] == expected_hash


def test_consumer_inbox_rejects_replayed_event_with_a_different_payload() -> None:
    connection = FakeConnection([FakeResult(), FakeResult([{"payload_sha256": "0" * 64}])])
    inbox = TenantConsumerInbox(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    with pytest.raises(ConsumerPayloadMismatchError):
        inbox.record_once(
            consumer_name="sse-projector",
            event_id=EVENT_ID,
            payload={"changed": True},
            processed_at=NOW,
        )


def test_retry_rejects_exception_text_to_avoid_persisting_payloads() -> None:
    repository = TenantOutboxRepository(  # type: ignore[arg-type]
        FakeConnection([]), ORGANIZATION_ID
    )

    with pytest.raises(ValueError, match="error_code"):
        repository.schedule_retry(
            OutboxLease(OUTBOX_ID, "publisher-a:opaque-attempt-token"),
            retry_at=NOW,
            error_code="broker response payload={secret}",
        )


@pytest.mark.parametrize("worker_id", ["", " ", "x" * 256])
def test_lease_rejects_invalid_worker_id(worker_id: str) -> None:
    repository = TenantOutboxRepository(  # type: ignore[arg-type]
        FakeConnection([]), ORGANIZATION_ID
    )

    with pytest.raises(ValueError, match="worker_id"):
        repository.lease_available(worker_id=worker_id, now=NOW)
