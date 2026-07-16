from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from netops_worker.config import WorkerSettings
from netops_worker.main import run_worker


class FakeWorkflowService:
    def __init__(self) -> None:
        self.calls = 0

    async def get_system_info(self, _: object) -> None:
        self.calls += 1


@dataclass
class FakeClient:
    workflow_service: FakeWorkflowService

    async def start_workflow(self, *_: object, **__: object) -> FakeWorkflowHandle:
        return FakeWorkflowHandle()


class FakeWorkflowHandle:
    async def result(self) -> str:
        return "worker-runtime-ready"


class FakeWorker:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeWorker:
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True


class FakePublisher:
    def __init__(self) -> None:
        self.sweeps = 0
        self.loop_started = asyncio.Event()

    def publish_once(self) -> object:
        self.sweeps += 1
        return object()

    async def run_until_stopped(self, stop_event: asyncio.Event) -> None:
        self.loop_started.set()
        await stop_event.wait()


class FailingPublisher(FakePublisher):
    async def run_until_stopped(self, _: asyncio.Event) -> None:
        self.loop_started.set()
        raise RuntimeError("publisher database connection failed")


def test_worker_checks_temporal_before_it_becomes_ready_and_drains_on_stop() -> None:
    async def exercise() -> None:
        service = FakeWorkflowService()
        client = FakeClient(workflow_service=service)
        worker = FakeWorker()
        stop_event = asyncio.Event()
        settings = WorkerSettings(health_host="127.0.0.1", health_port=0)

        async def connect(*_: object, **__: object) -> FakeClient:
            return client

        def create_worker(*_: object, **__: object) -> FakeWorker:
            return worker

        task = asyncio.create_task(
            run_worker(
                settings,
                stop_event=stop_event,
                client_connector=connect,
                worker_factory=create_worker,
            )
        )
        for _ in range(50):
            if worker.entered:
                break
            await asyncio.sleep(0.01)
        stop_event.set()
        await task

        assert service.calls == 1
        assert worker.entered is True
        assert worker.exited is True

    asyncio.run(exercise())


def test_worker_runs_the_configured_durable_outbox_loop_beside_temporal() -> None:
    async def exercise() -> None:
        service = FakeWorkflowService()
        client = FakeClient(workflow_service=service)
        worker = FakeWorker()
        publisher = FakePublisher()
        stop_event = asyncio.Event()
        settings = WorkerSettings(health_host="127.0.0.1", health_port=0)

        async def connect(*_: object, **__: object) -> FakeClient:
            return client

        def create_worker(*_: object, **__: object) -> FakeWorker:
            return worker

        task = asyncio.create_task(
            run_worker(
                settings,
                stop_event=stop_event,
                client_connector=connect,
                worker_factory=create_worker,
                outbox_publisher_factory=lambda _: publisher,  # type: ignore[return-value]
            )
        )
        await asyncio.wait_for(publisher.loop_started.wait(), timeout=2)
        stop_event.set()
        await task

        assert publisher.sweeps == 1
        assert worker.exited is True

    asyncio.run(exercise())


def test_worker_stops_and_surfaces_an_unexpected_publisher_loop_failure() -> None:
    async def exercise() -> None:
        service = FakeWorkflowService()
        client = FakeClient(workflow_service=service)
        worker = FakeWorker()
        publisher = FailingPublisher()
        settings = WorkerSettings(health_host="127.0.0.1", health_port=0)

        async def connect(*_: object, **__: object) -> FakeClient:
            return client

        def create_worker(*_: object, **__: object) -> FakeWorker:
            return worker

        with pytest.raises(RuntimeError, match="publisher database connection failed"):
            await run_worker(
                settings,
                client_connector=connect,
                worker_factory=create_worker,
                outbox_publisher_factory=lambda _: publisher,  # type: ignore[return-value]
            )

        assert publisher.sweeps == 1
        assert worker.exited is True

    asyncio.run(exercise())
