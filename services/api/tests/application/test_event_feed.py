"""Unit contracts for the persisted tenant-scoped SSE event reader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from netops_api.application.event_feed import EventCursorNotFoundError, TenantCaseEventFeed

ORGANIZATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c1")
ASSET_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c2")
EVENT_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c3")
CASE_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c4")
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class FakeMappings:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ):
        self._one = one
        self._rows = rows if rows is not None else ([] if one is None else [one])

    def one_or_none(self) -> dict[str, Any] | None:
        return self._one

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeResult:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ):
        self._mappings = FakeMappings(one=one, rows=rows)

    def mappings(self) -> FakeMappings:
        return self._mappings


class FakeConnection:
    def __init__(self, results: list[FakeResult]):
        self._results = iter(results)
        self.executed: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: dict[str, object]) -> FakeResult:
        self.executed.append((str(statement), parameters))
        return next(self._results)


def _event_row() -> dict[str, object]:
    return {
        "event_id": EVENT_ID,
        "case_id": CASE_ID,
        "event_type": "case.created.v1",
        "aggregate_version": 0,
        "transition_id": None,
        "actor_id": UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c5"),
        "correlation_id": UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c6"),
        "occurred_at": NOW,
    }


def test_initial_feed_page_reads_immutable_events_with_sql_asset_allow_list() -> None:
    connection = FakeConnection([FakeResult(rows=[_event_row()])])
    feed = TenantCaseEventFeed(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    events = feed.read_after(after_event_id=None, asset_ids=(ASSET_ID,), limit=10)

    assert events[0].event_id == EVENT_ID
    statement, parameters = connection.executed[0]
    assert "FROM case_events AS e" in statement
    assert "JOIN cases AS c" in statement
    assert "e.organization_id = :organization_id" in statement
    assert "c.asset_id IS NULL OR c.asset_id = ANY" in statement
    assert "ORDER BY e.occurred_at ASC, e.id ASC" in statement
    assert parameters == {
        "organization_id": ORGANIZATION_ID,
        "asset_ids": [ASSET_ID],
        "limit": 10,
    }


def test_recovery_cursor_is_scope_checked_before_ordered_replay() -> None:
    cursor = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c7")
    connection = FakeConnection(
        [FakeResult(one={"occurred_at": NOW}), FakeResult(rows=[_event_row()])]
    )
    feed = TenantCaseEventFeed(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    events = feed.read_after(after_event_id=cursor, asset_ids=(), limit=1)

    assert events == (events[0],)
    cursor_statement, cursor_parameters = connection.executed[0]
    replay_statement, replay_parameters = connection.executed[1]
    assert "e.id = :after_event_id" in cursor_statement
    assert "(e.occurred_at, e.id) > (:after_occurred_at, :after_event_id)" in replay_statement
    assert cursor_parameters["asset_ids"] == []
    assert replay_parameters["after_event_id"] == cursor
    assert replay_parameters["after_occurred_at"] == NOW


def test_cross_scope_or_cross_tenant_recovery_cursor_is_not_distinguished() -> None:
    cursor = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677c8")
    connection = FakeConnection([FakeResult()])
    feed = TenantCaseEventFeed(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    with pytest.raises(EventCursorNotFoundError) as error:
        feed.read_after(after_event_id=cursor, asset_ids=(), limit=10)

    assert error.value.event_id == cursor
    assert len(connection.executed) == 1
