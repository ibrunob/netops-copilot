from __future__ import annotations

import asyncio

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from netops_worker.runtime_probe import WorkerRuntimeProbeWorkflow, worker_runtime_probe


def test_runtime_probe_completes_on_a_real_temporal_sdk_worker() -> None:
    """Exercise workflow and activity polling against Temporal's SDK test service."""

    async def exercise() -> None:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            async with Worker(
                environment.client,
                task_queue="worker-runtime-sdk-test",
                workflows=[WorkerRuntimeProbeWorkflow],
                activities=[worker_runtime_probe],
            ):
                result = await environment.client.execute_workflow(
                    WorkerRuntimeProbeWorkflow.run,
                    id="worker-runtime-sdk-test",
                    task_queue="worker-runtime-sdk-test",
                )
        assert result == "worker-runtime-ready"

    asyncio.run(exercise())
