"""Real Temporal worker process lifecycle, intentionally without business workflows."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
from temporalio.client import Client
from temporalio.worker import Worker

from netops_worker.config import WorkerSettings, get_worker_settings
from netops_worker.health import WorkerHealth, start_health_server
from netops_worker.logging import configure_logging
from netops_worker.observability import configure_opentelemetry, lifecycle_span
from netops_worker.runtime_probe import WorkerRuntimeProbeWorkflow, worker_runtime_probe

logger = logging.getLogger(__name__)

ClientConnector = Callable[..., Awaitable[Client]]
WorkerFactory = Callable[..., Worker]


def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """Translate SIGTERM/SIGINT into one graceful worker shutdown request."""
    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(shutdown_signal, stop_event.set)


async def run_worker(
    settings: WorkerSettings,
    *,
    stop_event: asyncio.Event | None = None,
    client_connector: ClientConnector = Client.connect,
    worker_factory: WorkerFactory = Worker,
) -> None:
    """Connect, verify Temporal, poll its task queue, and drain cleanly on shutdown.

    No domain workflow is registered before M2/M6 defines the persistence and
    idempotency contracts. The one platform probe below is deliberately pure,
    bounded, and side-effect free; it proves this exact process can poll and
    complete both a Temporal workflow and activity before readiness is exposed.
    """
    configure_logging(settings)
    configure_opentelemetry(settings)
    stop = stop_event or asyncio.Event()
    health = WorkerHealth(settings=settings)
    server = await start_health_server(settings, health)
    try:
        with lifecycle_span("temporal.worker.connect"):
            client = await client_connector(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
            await client.workflow_service.get_system_info(GetSystemInfoRequest())

        worker = worker_factory(
            client,
            task_queue=settings.task_queue,
            workflows=[WorkerRuntimeProbeWorkflow],
            activities=[worker_runtime_probe],
            graceful_shutdown_timeout=timedelta(seconds=settings.graceful_shutdown_seconds),
        )
        async with worker:
            with lifecycle_span("temporal.worker.runtime_probe"):
                probe = await client.start_workflow(
                    WorkerRuntimeProbeWorkflow.run,
                    id=f"worker-runtime-probe-{uuid.uuid4()}",
                    task_queue=settings.task_queue,
                    execution_timeout=timedelta(seconds=15),
                )
                await probe.result()
            health.ready = True
            logger.info("temporal_worker_ready")
            with lifecycle_span("temporal.worker.runtime"):
                await stop.wait()
            health.ready = False
            logger.info("temporal_worker_shutdown_requested")
    finally:
        health.ready = False
        server.close()
        await server.wait_closed()
        logger.info("temporal_worker_stopped")


async def async_main(settings: WorkerSettings | None = None) -> None:
    """Run the worker with POSIX signal-driven graceful shutdown."""
    resolved_settings = settings or get_worker_settings()
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    await run_worker(resolved_settings, stop_event=stop_event)


def run() -> None:
    """Console-script entry point used by the worker container."""
    asyncio.run(async_main())
