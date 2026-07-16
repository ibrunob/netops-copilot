"""Fail-closed ClamAV adapter for quarantined private artifacts.

The adapter speaks clamd's ``INSTREAM`` protocol directly.  It never sends an
object URL or a filename to ClamAV, does not retain the stream, and converts
all daemon/network failures into the bounded ``scan.unavailable`` outcome.
Callers must supply a private reader: this module intentionally does not grow
credentials, object-store policy, or a production fallback scanner.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from netops_worker.artifact_processing import (
    ArtifactProcessingJob,
    ArtifactScanner,
    ScanDisposition,
    ScanResult,
)
from netops_worker.config import WorkerSettings

_CHUNK_SIZE = 64 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024


class PrivateArtifactReader(Protocol):
    """Open a completed quarantined artifact without revealing its location."""

    def open_for_scan(self, job: ArtifactProcessingJob) -> BinaryIO:
        """Return a binary stream whose lifetime belongs to the scanner call."""


class ClamAvSocketFactory(Protocol):
    """Small seam for testing the clamd protocol without a daemon."""

    def create_connection(
        self, address: tuple[str, int], timeout: float
    ) -> socket.socket:
        """Open the private clamd TCP connection."""


@dataclass(frozen=True, slots=True)
class ClamAvSettings:
    """Bounded connection and stream limits for the scanner boundary."""

    address: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_scan_bytes: int

    @classmethod
    def from_worker_settings(cls, settings: WorkerSettings) -> ClamAvSettings:
        """Copy the non-secret ClamAV configuration from the worker settings."""
        return cls(
            address=settings.clamav_address,
            connect_timeout_seconds=settings.clamav_connect_timeout_seconds,
            read_timeout_seconds=settings.clamav_read_timeout_seconds,
            max_scan_bytes=settings.clamav_max_scan_bytes,
        )

    def host_and_port(self) -> tuple[str, int]:
        """Return the validated host/port tuple used by ``socket``."""
        host, port = self.address.rsplit(":", maxsplit=1)
        return host, int(port)


class _SocketModuleFactory:
    def create_connection(
        self, address: tuple[str, int], timeout: float
    ) -> socket.socket:
        return socket.create_connection(address, timeout=timeout)


class ClamAvArtifactScanner(ArtifactScanner):
    """Stream a private artifact into clamd and fail closed on any uncertainty."""

    def __init__(
        self,
        *,
        reader: PrivateArtifactReader,
        settings: ClamAvSettings,
        socket_factory: ClamAvSocketFactory | None = None,
    ) -> None:
        self._reader = reader
        self._settings = settings
        self._socket_factory = socket_factory or _SocketModuleFactory()

    def scan(self, job: ArtifactProcessingJob) -> ScanResult:
        """Return a bounded verdict; do not expose daemon text or input bytes."""
        try:
            with self._reader.open_for_scan(job) as source:
                return self._scan_stream(source)
        except (OSError, ValueError, UnicodeError):
            return ScanResult(ScanDisposition.UNAVAILABLE, "scan.unavailable")

    def _scan_stream(self, source: BinaryIO) -> ScanResult:
        scanned = 0
        with self._socket_factory.create_connection(
            self._settings.host_and_port(), self._settings.connect_timeout_seconds
        ) as connection:
            connection.settimeout(self._settings.read_timeout_seconds)
            connection.sendall(b"zINSTREAM\0")
            while chunk := source.read(_CHUNK_SIZE):
                scanned += len(chunk)
                if scanned > self._settings.max_scan_bytes:
                    return ScanResult(ScanDisposition.REJECTED, "scan.size_limit_exceeded")
                connection.sendall(struct.pack("!I", len(chunk)))
                connection.sendall(chunk)
            connection.sendall(struct.pack("!I", 0))
            response = _read_response(connection)
        return _parse_response(response)


def _read_response(connection: socket.socket) -> bytes:
    """Read one bounded NUL-terminated clamd response."""
    response = bytearray()
    while len(response) < _MAX_RESPONSE_BYTES:
        fragment = connection.recv(min(512, _MAX_RESPONSE_BYTES - len(response)))
        if not fragment:
            raise OSError("clamd closed without a response")
        response.extend(fragment)
        if b"\0" in fragment:
            return bytes(response).split(b"\0", maxsplit=1)[0]
    raise OSError("clamd response exceeded the protocol limit")


def _parse_response(response: bytes) -> ScanResult:
    """Map only documented clamd verdict shapes to safe stable codes."""
    normalized = response.strip()
    if normalized.endswith(b": OK"):
        return ScanResult(ScanDisposition.CLEAN, "scan.clean")
    if normalized.endswith(b" FOUND"):
        return ScanResult(ScanDisposition.REJECTED, "scan.malware_detected")
    return ScanResult(ScanDisposition.UNAVAILABLE, "scan.unavailable")
