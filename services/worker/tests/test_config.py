from __future__ import annotations

import pytest
from pydantic import ValidationError

from netops_worker.config import WorkerSettings


@pytest.mark.parametrize("endpoint", ["", "temporal", "http://temporal:7233", "temporal:0"])
def test_temporal_endpoint_requires_host_and_port(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="host:port"):
        WorkerSettings(temporal_address=endpoint)


@pytest.mark.parametrize("queue", ["", " queue", "queue with spaces", "queue!"])
def test_task_queue_is_bounded_and_safe_for_operational_metadata(queue: str) -> None:
    with pytest.raises(ValidationError, match="task queue"):
        WorkerSettings(task_queue=queue)
