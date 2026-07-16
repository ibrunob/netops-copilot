"""Recoverable tenant-scoped reads of immutable case events for SSE consumers.

This reader uses ``case_events`` rather than outbox delivery state: the outbox
can be retried or published by several workers, while case events are the
single immutable business history that an operator-facing feed must replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Result, text


class EventCursorNotFoundError(LookupError):
    """Raised when a recovery cursor is not visible in this tenant/asset scope."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__("The event recovery cursor is not available.")
        self.event_id = event_id


@dataclass(frozen=True, slots=True)
class CaseFeedEvent:
    """A safe immutable event envelope suitable for an operator SSE feed."""

    event_id: UUID
    case_id: UUID
    event_type: str
    aggregate_version: int
    transition_id: UUID | None
    actor_id: UUID
    correlation_id: UUID
    occurred_at: datetime


class TenantCaseEventFeed:
    """Read ordered immutable events from an RLS-scoped tenant connection.

    ``asset_ids`` is an allow-list.  An empty sequence intentionally exposes
    only organization-level (assetless) cases; it is never treated as an
    all-assets grant.  The cursor must itself be visible through the same
    predicate, which prevents a cross-asset or cross-tenant cursor from
    influencing replay position.
    """

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def read_after(
        self,
        *,
        after_event_id: UUID | None,
        asset_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[CaseFeedEvent, ...]:
        """Return one finite, strictly ordered replay page after the given cursor."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        parameters: dict[str, object] = {
            "organization_id": self._organization_id,
            "asset_ids": list(asset_ids),
            "limit": limit,
        }
        if after_event_id is None:
            rows = self._mappings(self._connection.execute(_SELECT_INITIAL_PAGE, parameters))
        else:
            parameters["after_event_id"] = after_event_id
            cursor = self._one_or_none(self._connection.execute(_SELECT_VISIBLE_CURSOR, parameters))
            if cursor is None:
                raise EventCursorNotFoundError(after_event_id)
            parameters["after_occurred_at"] = _datetime(cursor["occurred_at"])
            rows = self._mappings(self._connection.execute(_SELECT_PAGE_AFTER_CURSOR, parameters))
        return tuple(_feed_event(row) for row in rows)

    @staticmethod
    def _mappings(result: Result[Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in result.mappings().all()]

    @staticmethod
    def _one_or_none(result: Result[Any]) -> dict[str, Any] | None:
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)


def _feed_event(row: dict[str, Any]) -> CaseFeedEvent:
    return CaseFeedEvent(
        event_id=_uuid(row["event_id"]),
        case_id=_uuid(row["case_id"]),
        event_type=str(row["event_type"]),
        aggregate_version=int(row["aggregate_version"]),
        transition_id=_optional_uuid(row["transition_id"]),
        actor_id=_uuid(row["actor_id"]),
        correlation_id=_uuid(row["correlation_id"]),
        occurred_at=_datetime(row["occurred_at"]),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else _uuid(value)


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("Database datetime columns must be returned as datetime values.")
    return value


_VISIBLE_CASES = """
  c.organization_id = :organization_id
  AND (c.asset_id IS NULL OR c.asset_id = ANY(CAST(:asset_ids AS uuid[])))
"""

_SELECT_INITIAL_PAGE = text(
    f"""
    SELECT e.id AS event_id, e.case_id, e.event_type, e.aggregate_version,
           e.transition_id, e.actor_id, e.correlation_id, e.occurred_at
    FROM case_events AS e
    JOIN cases AS c
      ON c.organization_id = e.organization_id AND c.id = e.case_id
    WHERE e.organization_id = :organization_id
      AND {_VISIBLE_CASES}
    ORDER BY e.occurred_at ASC, e.id ASC
    LIMIT :limit
    """
)

_SELECT_VISIBLE_CURSOR = text(
    f"""
    SELECT e.occurred_at
    FROM case_events AS e
    JOIN cases AS c
      ON c.organization_id = e.organization_id AND c.id = e.case_id
    WHERE e.organization_id = :organization_id
      AND e.id = :after_event_id
      AND {_VISIBLE_CASES}
    """
)

_SELECT_PAGE_AFTER_CURSOR = text(
    f"""
    SELECT e.id AS event_id, e.case_id, e.event_type, e.aggregate_version,
           e.transition_id, e.actor_id, e.correlation_id, e.occurred_at
    FROM case_events AS e
    JOIN cases AS c
      ON c.organization_id = e.organization_id AND c.id = e.case_id
    WHERE e.organization_id = :organization_id
      AND {_VISIBLE_CASES}
      AND (e.occurred_at, e.id) > (:after_occurred_at, :after_event_id)
    ORDER BY e.occurred_at ASC, e.id ASC
    LIMIT :limit
    """
)
