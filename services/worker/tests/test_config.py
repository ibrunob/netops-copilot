from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from netops_worker.config import WorkerSettings


@pytest.mark.parametrize("endpoint", ["", "temporal", "http://temporal:7233", "temporal:0"])
def test_temporal_endpoint_requires_host_and_port(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="host:port"):
        WorkerSettings(temporal_address=endpoint)


@pytest.mark.parametrize("endpoint", ["", "clamav", "http://clamav:3310", "clamav:0"])
def test_clamav_endpoint_requires_host_and_port(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="host:port"):
        WorkerSettings(clamav_address=endpoint)


@pytest.mark.parametrize("queue", ["", " queue", "queue with spaces", "queue!"])
def test_task_queue_is_bounded_and_safe_for_operational_metadata(queue: str) -> None:
    with pytest.raises(ValidationError, match="task queue"):
        WorkerSettings(task_queue=queue)


def test_enabled_outbox_publisher_requires_database_and_explicit_tenant_scope() -> None:
    with pytest.raises(ValidationError, match="database_url and outbox_organization_id"):
        WorkerSettings(outbox_publisher_enabled=True)

    settings = WorkerSettings(
        outbox_publisher_enabled=True,
        database_url="postgresql+psycopg://netops_app:secret@postgres:5432/netops",
        outbox_organization_id=UUID("00000000-0000-0000-0000-000000000001"),
    )
    assert settings.outbox_organization_id == UUID("00000000-0000-0000-0000-000000000001")
