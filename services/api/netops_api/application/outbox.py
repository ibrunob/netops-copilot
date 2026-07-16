"""Transactional outbox delivery bookkeeping and consumer deduplication.

The case repository writes an outbox row in the same transaction as its business
event.  This module deliberately does *not* publish to a broker: a worker leases
rows, invokes its transport, and then records either delivery or a scheduled
retry.  The SQL only mutates the delivery fields permitted by the database
trigger, while all tenant-owned operations remain scoped to an already verified
tenant connection.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Result, text

_DELIVERY_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")


class OutboxLeaseLostError(RuntimeError):
    """Raised when a worker tries to settle an outbox event it no longer leases."""

    def __init__(self, outbox_id: UUID) -> None:
        super().__init__(f"Outbox event {outbox_id} is not leased by this worker.")
        self.outbox_id = outbox_id


class ConsumerPayloadMismatchError(ValueError):
    """Raised when an event replay changes the payload seen by one consumer."""

    def __init__(self, event_id: UUID, consumer_name: str) -> None:
        super().__init__("The consumer receipt exists with a different payload fingerprint.")
        self.event_id = event_id
        self.consumer_name = consumer_name


@dataclass(frozen=True, slots=True)
class LeasedOutboxEvent:
    """Immutable business event plus the delivery lease owned by one worker."""

    outbox_id: UUID
    organization_id: UUID
    case_id: UUID
    case_event_id: UUID
    event_type: str
    aggregate_version: int
    correlation_id: UUID
    payload: dict[str, object]
    available_at: datetime
    created_at: datetime
    attempt_count: int
    locked_at: datetime
    locked_by: str

    @property
    def lease(self) -> OutboxLease:
        """Return the opaque ownership token needed to settle this delivery."""
        return OutboxLease(self.outbox_id, self.locked_by)


@dataclass(frozen=True, slots=True)
class OutboxLease:
    """Opaque per-attempt lease identity required to settle a delivery."""

    outbox_id: UUID
    lease_token: str

    def __post_init__(self) -> None:
        if not self.lease_token.strip() or len(self.lease_token) > 255:
            raise ValueError("lease_token must contain 1 to 255 non-blank characters.")


class TenantOutboxRepository:
    """Lease and settle outbox rows for one tenant inside a tenant transaction.

    The caller should use a unique ``worker_id`` per running publisher instance.
    ``FOR UPDATE SKIP LOCKED`` lets multiple workers drain a tenant without
    duplicate concurrent delivery; an expired lease can be recovered after a
    process crash.
    """

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def lease_available(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int = 50,
        lease_for: timedelta = timedelta(minutes=1),
    ) -> tuple[LeasedOutboxEvent, ...]:
        """Atomically lease ready rows and increment their attempt count once."""
        _validate_worker_id(worker_id)
        _validate_utc(now, field_name="now")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive.")

        lease_token = f"{worker_id}:{uuid4()}"
        rows = self._mappings(
            self._connection.execute(
                _LEASE_AVAILABLE,
                {
                    "organization_id": self._organization_id,
                    "lease_token": lease_token,
                    "now": now,
                    "lease_expired_at": now - lease_for,
                    "limit": limit,
                },
            )
        )
        return tuple(_leased_event(row) for row in rows)

    def mark_published(self, lease: OutboxLease, *, published_at: datetime) -> None:
        """Record one successful publish; stale or foreign leases fail closed."""
        _validate_utc(published_at, field_name="published_at")
        result = self._connection.execute(
            _MARK_PUBLISHED,
            {
                "organization_id": self._organization_id,
                "outbox_id": lease.outbox_id,
                "lease_token": lease.lease_token,
                "published_at": published_at,
            },
        )
        if result.rowcount != 1:
            raise OutboxLeaseLostError(lease.outbox_id)

    def schedule_retry(
        self,
        lease: OutboxLease,
        *,
        retry_at: datetime,
        error_code: str,
    ) -> None:
        """Release a failed delivery lease and make the event available later."""
        _validate_utc(retry_at, field_name="retry_at")
        if not _DELIVERY_ERROR_CODE.fullmatch(error_code):
            raise ValueError("error_code must be a 1 to 128 character safe delivery code.")
        result = self._connection.execute(
            _SCHEDULE_RETRY,
            {
                "organization_id": self._organization_id,
                "outbox_id": lease.outbox_id,
                "lease_token": lease.lease_token,
                "retry_at": retry_at,
                # Never persist exception text: it can contain event payloads,
                # URLs, or credentials. Only a bounded safe code is retained.
                "error_code": error_code,
            },
        )
        if result.rowcount != 1:
            raise OutboxLeaseLostError(lease.outbox_id)

    def _mappings(self, result: Result[Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in result.mappings().all()]


class TenantConsumerInbox:
    """Persist one immutable consumer receipt, returning whether it was new."""

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def record_once(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        payload: object,
        processed_at: datetime,
    ) -> bool:
        """Insert a dedup receipt before side effects; ``False`` means replay."""
        if not consumer_name.strip() or len(consumer_name) > 255:
            raise ValueError("consumer_name must contain 1 to 255 non-blank characters.")
        _validate_utc(processed_at, field_name="processed_at")
        try:
            canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON serializable.") from exc
        payload_sha256 = sha256(canonical_payload.encode("utf-8")).hexdigest()
        result = self._connection.execute(
            _INSERT_CONSUMER_INBOX,
            {
                "inbox_id": uuid4(),
                "organization_id": self._organization_id,
                "consumer_name": consumer_name.strip(),
                "event_id": event_id,
                "payload_sha256": payload_sha256,
                "processed_at": processed_at,
            },
        )
        if result.mappings().one_or_none() is not None:
            return True
        existing = self._connection.execute(
            _SELECT_CONSUMER_PAYLOAD_SHA256,
            {
                "organization_id": self._organization_id,
                "consumer_name": consumer_name.strip(),
                "event_id": event_id,
            },
        ).mappings().one_or_none()
        if existing is None:
            raise RuntimeError("Consumer deduplication conflict did not retain its receipt.")
        if str(existing["payload_sha256"]) != payload_sha256:
            raise ConsumerPayloadMismatchError(event_id, consumer_name)
        return False


def _validate_worker_id(worker_id: str) -> None:
    if not worker_id.strip() or len(worker_id) > 200:
        raise ValueError("worker_id must contain 1 to 200 non-blank characters.")


def _validate_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is not UTC or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC.")


def _leased_event(row: dict[str, Any]) -> LeasedOutboxEvent:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("outbox payload must be a JSON object.")
    locked_at = row["locked_at"]
    locked_by = row["locked_by"]
    if not isinstance(locked_at, datetime) or not isinstance(locked_by, str):
        raise RuntimeError("Leased outbox event did not retain its delivery lease.")
    return LeasedOutboxEvent(
        outbox_id=_uuid(row["id"]),
        organization_id=_uuid(row["organization_id"]),
        case_id=_uuid(row["case_id"]),
        case_event_id=_uuid(row["case_event_id"]),
        event_type=str(row["event_type"]),
        aggregate_version=int(row["aggregate_version"]),
        correlation_id=_uuid(row["correlation_id"]),
        payload=payload,
        available_at=row["available_at"],
        created_at=row["created_at"],
        attempt_count=int(row["attempt_count"]),
        locked_at=locked_at,
        locked_by=locked_by,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


_LEASE_AVAILABLE = text(
    """
    WITH candidates AS (
      SELECT id
      FROM outbox_events
      WHERE organization_id = :organization_id
        AND published_at IS NULL
        AND available_at <= :now
        AND (locked_at IS NULL OR locked_at <= :lease_expired_at)
      ORDER BY available_at ASC, created_at ASC, id ASC
      LIMIT :limit
      FOR UPDATE SKIP LOCKED
    )
    UPDATE outbox_events AS event
    SET locked_at = :now,
        locked_by = :lease_token,
        attempt_count = event.attempt_count + 1
    FROM candidates
    WHERE event.id = candidates.id
      AND event.organization_id = :organization_id
    RETURNING event.id, event.organization_id, event.case_id, event.case_event_id,
              event.event_type, event.aggregate_version, event.correlation_id,
              event.payload, event.available_at, event.created_at, event.attempt_count,
              event.locked_at, event.locked_by
    """
)
_MARK_PUBLISHED = text(
    """
    UPDATE outbox_events
    SET published_at = :published_at,
        locked_at = NULL,
        locked_by = NULL,
        last_error = NULL
    WHERE organization_id = :organization_id
      AND id = :outbox_id
      AND published_at IS NULL
      AND locked_by = :lease_token
    """
)
_SCHEDULE_RETRY = text(
    """
    UPDATE outbox_events
    SET available_at = :retry_at,
        locked_at = NULL,
        locked_by = NULL,
        last_error = :error_code
    WHERE organization_id = :organization_id
      AND id = :outbox_id
      AND published_at IS NULL
      AND locked_by = :lease_token
    """
)
_INSERT_CONSUMER_INBOX = text(
    """
    INSERT INTO consumer_inbox (
      id, organization_id, consumer_name, event_id, payload_sha256, processed_at
    ) VALUES (
      :inbox_id, :organization_id, :consumer_name, :event_id, :payload_sha256, :processed_at
    )
    ON CONFLICT (organization_id, consumer_name, event_id) DO NOTHING
    RETURNING id
    """
)
_SELECT_CONSUMER_PAYLOAD_SHA256 = text(
    """
    SELECT payload_sha256
    FROM consumer_inbox
    WHERE organization_id = :organization_id
      AND consumer_name = :consumer_name
      AND event_id = :event_id
    """
)
