"""Small dependency-free HTTP liveness and readiness server for the worker."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Final

from netops_worker.config import WorkerSettings

_MAX_REQUEST_BYTES: Final = 8_192


@dataclass
class WorkerHealth:
    """Mutable worker state exposed only as coarse operational health."""

    settings: WorkerSettings
    ready: bool = False

    def response(self, path: str) -> tuple[int, dict[str, object]]:
        """Build a safe health response without exposing Temporal endpoints or errors."""
        base: dict[str, object] = {
            "service": self.settings.service_name,
            "version": self.settings.service_version,
        }
        if path == "/healthz":
            return 200, {"status": "ok", **base}
        if path == "/readyz":
            if self.ready:
                return 200, {"status": "ok", **base, "components": {"temporal": "ready"}}
            return 503, {
                "status": "unavailable",
                **base,
                "components": {"temporal": "unavailable"},
            }
        return 404, {"status": "not_found"}


async def start_health_server(
    settings: WorkerSettings, health: WorkerHealth
) -> asyncio.AbstractServer:
    """Serve only health probes; malformed requests never expose runtime details."""

    async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status: int
        payload: dict[str, object]
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            if len(request) > _MAX_REQUEST_BYTES:
                raise ValueError("request too large")
            request_line = request.split(b"\r\n", maxsplit=1)[0].decode("ascii")
            method, target, _ = request_line.split(" ", maxsplit=2)
            if method != "GET":
                status, payload = 405, {"status": "method_not_allowed"}
            else:
                path = target.split("?", maxsplit=1)[0]
                status, payload = health.response(path)
        except (UnicodeDecodeError, ValueError, asyncio.IncompleteReadError):
            status, payload = 400, {"status": "bad_request"}

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            503: "Service Unavailable",
        }[status]
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode("ascii")
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return await asyncio.start_server(handle_connection, settings.health_host, settings.health_port)
