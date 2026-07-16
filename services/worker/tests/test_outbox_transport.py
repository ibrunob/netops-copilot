from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from netops_api.application.outbox import LeasedOutboxEvent
from netops_worker.outbox_transport import DurableInboxReceiptTransport

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000006")
OUTBOX_ID = UUID("00000000-0000-0000-0000-000000000002")
CASE_ID = UUID("00000000-0000-0000-0000-000000000003")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000004")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000005")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


class FakeDatabase:
    def __init__(self) -> None:
        self.organizations: list[UUID] = []

    def tenant_connection(self, organization_id: UUID) -> Any:
        self.organizations.append(organization_id)
        return nullcontext(object())


class FakeInbox:
    def __init__(self) -> None:
        self.receipts: list[dict[str, object]] = []

    def record_once(self, **kwargs: object) -> bool:
        self.receipts.append(kwargs)
        return len(self.receipts) == 1


def event(*, organization_id: UUID = ORGANIZATION_ID) -> LeasedOutboxEvent:
    return LeasedOutboxEvent(
        outbox_id=OUTBOX_ID,
        organization_id=organization_id,
        case_id=CASE_ID,
        case_event_id=EVENT_ID,
        event_type="case.created.v1",
        aggregate_version=0,
        correlation_id=CORRELATION_ID,
        payload={"case_id": str(CASE_ID), "version": 0},
        available_at=NOW,
        created_at=NOW,
        attempt_count=1,
        locked_at=NOW,
        locked_by="publisher-a:opaque-token",
    )


def test_durable_transport_records_exact_case_event_receipt_in_tenant_scope() -> None:
    database = FakeDatabase()
    inbox = FakeInbox()
    transport = DurableInboxReceiptTransport(
        database,  # type: ignore[arg-type]
        ORGANIZATION_ID,
        inbox_factory=lambda *_: inbox,
        clock=lambda: NOW,
    )

    transport.publish(event())
    transport.publish(event())

    assert database.organizations == [ORGANIZATION_ID, ORGANIZATION_ID]
    assert [receipt["event_id"] for receipt in inbox.receipts] == [EVENT_ID, EVENT_ID]
    assert all(receipt["consumer_name"] == "case-event-receipt.v1" for receipt in inbox.receipts)
    assert all(receipt["payload"] == event().payload for receipt in inbox.receipts)


def test_durable_transport_fails_closed_for_another_organization() -> None:
    database = FakeDatabase()
    transport = DurableInboxReceiptTransport(
        database,  # type: ignore[arg-type]
        ORGANIZATION_ID,
        inbox_factory=lambda *_: FakeInbox(),
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="organization"):
        transport.publish(event(organization_id=OTHER_ORGANIZATION_ID))

    assert database.organizations == []
