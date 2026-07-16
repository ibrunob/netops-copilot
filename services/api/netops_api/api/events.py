"""Recoverable Server-Sent Events replay for authorized case history."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse

from netops_api.api.cases import _require_case_read_access
from netops_api.api.errors import ApiError, ErrorEnvelope
from netops_api.application.event_feed import (
    CaseFeedEvent,
    EventCursorNotFoundError,
    TenantCaseEventFeed,
)
from netops_api.core.dependencies import PrincipalDependency, TenantConnectionDependency

router = APIRouter(prefix="/v1/events", tags=["events"])


class CaseEventFeed(Protocol):
    """Read-only feed port that makes route tests independent of PostgreSQL."""

    def read_after(
        self,
        *,
        after_event_id: UUID | None,
        asset_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[CaseFeedEvent, ...]: ...


def get_case_event_feed(
    principal: PrincipalDependency,
    connection: TenantConnectionDependency,
) -> TenantCaseEventFeed:
    """Create a feed from the signed tenant and its RLS-bound transaction."""
    return TenantCaseEventFeed(connection, principal.organization_id)


CaseEventFeedDependency = Annotated[CaseEventFeed, Depends(get_case_event_feed)]
LastEventId = Annotated[str | None, Header(alias="Last-Event-ID", max_length=64)]


@router.get(
    "",
    response_class=StreamingResponse,
    responses={
        401: {"model": ErrorEnvelope, "description": "A signed access token is required."},
        403: {"model": ErrorEnvelope, "description": "The signed user lacks case permission."},
        409: {"model": ErrorEnvelope, "description": "The recovery cursor is unavailable."},
        422: {"model": ErrorEnvelope, "description": "The recovery cursor is invalid."},
    },
)
def replay_case_events(
    principal: PrincipalDependency,
    feed: CaseEventFeedDependency,
    last_event_id: LastEventId = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> StreamingResponse:
    """Return a bounded ordered event replay; reconnect using the final SSE ID.

    The response intentionally ends after the persisted replay page rather than
    holding a database transaction open. Clients reconnect with ``Last-Event-ID``
    to recover pages and receive later commits, which remains correct across API
    restarts and outbox-worker crashes.
    """
    _require_case_read_access(principal)
    cursor = _parse_last_event_id(last_event_id)
    try:
        events = feed.read_after(
            after_event_id=cursor,
            asset_ids=tuple(sorted(principal.asset_ids, key=str)),
            limit=limit,
        )
    except EventCursorNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="event_cursor_unavailable",
            message="The event recovery cursor is not available.",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_event_cursor",
            message="The event recovery cursor is invalid.",
        ) from exc
    return StreamingResponse(
        _sse_events(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_last_event_id(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_event_cursor",
            message="The event recovery cursor is invalid.",
        ) from exc


def _sse_events(events: tuple[CaseFeedEvent, ...]) -> Iterator[str]:
    for event in events:
        payload = {
            "event_id": str(event.event_id),
            "case_id": str(event.case_id),
            "event_type": event.event_type,
            "aggregate_version": event.aggregate_version,
            "transition_id": str(event.transition_id) if event.transition_id else None,
            "actor_id": str(event.actor_id),
            "correlation_id": str(event.correlation_id),
            "occurred_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        }
        encoded_payload = json.dumps(payload, separators=(",", ":"))
        yield f"id: {event.event_id}\nevent: case\ndata: {encoded_payload}\n\n"
