"""Real Temporal worker process lifecycle, intentionally without business workflows."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
from temporalio.client import Client
from temporalio.worker import Worker

from netops_api.core.database import TenantDatabase
from netops_worker.config import WorkerSettings, get_worker_settings
from netops_worker.health import WorkerHealth, start_health_server
from netops_worker.logging import configure_logging
from netops_worker.observability import configure_opentelemetry, lifecycle_span
from netops_worker.outbox_publisher import OutboxPublisher, OutboxPublisherSettings
from netops_worker.outbox_transport import DurableInboxReceiptTransport
from netops_worker.runtime_probe import WorkerRuntimeProbeWorkflow, worker_runtime_probe

logger = logging.getLogger(__name__)

ClientConnector = Callable[..., Awaitable[Client]]
WorkerFactory = Callable[..., Worker]


class OutboxPublisherFactory(Protocol):
    """Build the concrete publisher only after explicit worker configuration."""

    def __call__(self, settings: WorkerSettings) -> OutboxPublisher | None: ...


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
    outbox_publisher_factory: OutboxPublisherFactory | None = None,
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
    publisher_task: asyncio.Task[None] | None = None
    publisher: OutboxPublisher | None = None
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
            publisher = (outbox_publisher_factory or build_outbox_publisher)(settings)
            if publisher is not None:
                # Complete one database-backed sweep before readiness. This
                # proves the configured RLS scope and receipt consumer work
                # before the long-running loop starts beside Temporal.
                await asyncio.to_thread(publisher.publish_once)
                publisher_task = asyncio.create_task(publisher.run_until_stopped(stop))
            health.ready = True
            logger.info("temporal_worker_ready")
            with lifecycle_span("temporal.worker.runtime"):
                if publisher_task is None:
                    await stop.wait()
                else:
                    try:
                        await _wait_for_stop_or_publisher_failure(stop, publisher_task)
                    except Exception:
                        health.ready = False
                        stop.set()
                        raise
            health.ready = False
            logger.info("temporal_worker_shutdown_requested")
    finally:
        health.ready = False
        stop.set()
        if publisher_task is not None:
            with contextlib.suppress(Exception):
                await publisher_task
        server.close()
        await server.wait_closed()
        logger.info("temporal_worker_stopped")


def build_outbox_publisher(settings: WorkerSettings) -> OutboxPublisher | None:
    """Construct the concrete durable publisher only when the scope is explicit."""
    if not settings.outbox_publisher_enabled:
        return None
    if settings.database_url is None or settings.outbox_organization_id is None:
        raise RuntimeError("Enabled outbox publisher is missing its database tenant scope.")
    database = TenantDatabase.from_url(settings.database_url.get_secret_value())
    return OutboxPublisher(
        database=database,
        organization_id=settings.outbox_organization_id,
        transport=DurableInboxReceiptTransport(
            database,
            settings.outbox_organization_id,
            consumer_name=settings.outbox_consumer_name,
        ),
        settings=OutboxPublisherSettings(
            worker_id=settings.outbox_worker_id,
            batch_size=settings.outbox_batch_size,
            lease_for=timedelta(seconds=settings.outbox_lease_seconds),
            retry_after=timedelta(seconds=settings.outbox_retry_seconds),
            poll_interval=timedelta(seconds=settings.outbox_poll_seconds),
        ),
    )


async def _wait_for_stop_or_publisher_failure(
    stop_event: asyncio.Event, publisher_task: asyncio.Task[None]
) -> None:
    """Observe the background publisher so an unexpected exit cannot be hidden."""
    stop_waiter = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            {stop_waiter, publisher_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if publisher_task in done:
            await publisher_task
            if not stop_event.is_set():
                raise RuntimeError("Outbox publisher stopped unexpectedly.")
    finally:
        if not stop_waiter.done():
            stop_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_waiter


async def async_main(settings: WorkerSettings | None = None) -> None:
    """Run the worker with POSIX signal-driven graceful shutdown."""
    resolved_settings = settings or get_worker_settings()
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    await run_worker(resolved_settings, stop_event=stop_event)


def run() -> None:
    """Console-script entry point used by the worker container."""
    asyncio.run(async_main())
