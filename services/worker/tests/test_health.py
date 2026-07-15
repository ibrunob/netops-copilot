from __future__ import annotations

import asyncio
import json

from netops_worker.config import WorkerSettings
from netops_worker.health import WorkerHealth, start_health_server


def test_readiness_requires_an_active_temporal_worker() -> None:
    health = WorkerHealth(WorkerSettings())

    assert health.response("/healthz")[0] == 200
    assert health.response("/readyz") == (
        503,
        {
            "status": "unavailable",
            "service": "netops-worker",
            "version": "0.1.0",
            "components": {"temporal": "unavailable"},
        },
    )

    health.ready = True
    assert health.response("/readyz")[0] == 200


def test_health_server_returns_safe_http_responses() -> None:
    async def exercise() -> None:
        settings = WorkerSettings(health_host="127.0.0.1", health_port=0)
        health = WorkerHealth(settings=settings, ready=True)
        server = await start_health_server(settings, health)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /readyz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        response = await reader.read()
        server.close()
        await server.wait_closed()

        header, body = response.split(b"\r\n\r\n", maxsplit=1)
        assert header.startswith(b"HTTP/1.1 200 OK")
        assert json.loads(body) == {
            "status": "ok",
            "service": "netops-worker",
            "version": "0.1.0",
            "components": {"temporal": "ready"},
        }

    asyncio.run(exercise())
