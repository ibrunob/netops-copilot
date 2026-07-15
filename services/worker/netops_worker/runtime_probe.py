"""A bounded platform-only Temporal probe used to verify worker readiness."""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


@activity.defn(name="netops.worker.runtime-probe.activity")
async def worker_runtime_probe() -> str:
    """Complete an idempotent, side-effect-free activity on this worker process."""
    return "worker-runtime-ready"


@workflow.defn(name="netops.worker.runtime-probe.workflow")
class WorkerRuntimeProbeWorkflow:
    """Prove the Temporal workflow and activity pollers are both operational.

    This is platform plumbing, not a triage workflow. It contains no network or
    storage I/O, uses one idempotent activity, and allows no retries so startup
    failure remains prompt and visible to the orchestrator.
    """

    @workflow.run
    async def run(self) -> str:
        """Execute the worker-local probe with a strict server-side timeout."""
        return await workflow.execute_activity(
            worker_runtime_probe,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
