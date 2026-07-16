from __future__ import annotations

import io
from dataclasses import dataclass, field
from uuid import UUID

from netops_worker.artifact_processing import (
    ArtifactClassification,
    ArtifactKind,
    ArtifactProcessingJob,
    ScanDisposition,
)
from netops_worker.clamav import ClamAvArtifactScanner, ClamAvSettings


def completed_job() -> ArtifactProcessingJob:
    return ArtifactProcessingJob(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        case_id=UUID("00000000-0000-0000-0000-000000000002"),
        artifact_id=UUID("00000000-0000-0000-0000-000000000003"),
        artifact_kind=ArtifactKind.NETWORK_CONFIGURATION,
        classification=ArtifactClassification.RAW,
        sha256_hex="a" * 64,
    )


@dataclass
class FakeReader:
    content: bytes
    jobs: list[object] = field(default_factory=list)

    def open_for_scan(self, job: object) -> io.BytesIO:
        self.jobs.append(job)
        return io.BytesIO(self.content)


@dataclass
class FakeSocket:
    response: bytes
    sent: bytearray = field(default_factory=bytearray)
    timeout: float | None = None

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, _: int) -> bytes:
        response, self.response = self.response, b""
        return response


@dataclass
class FakeSocketFactory:
    socket: FakeSocket
    addresses: list[tuple[str, int]] = field(default_factory=list)

    def create_connection(self, address: tuple[str, int], timeout: float) -> FakeSocket:
        assert timeout == 5
        self.addresses.append(address)
        return self.socket


def scanner(reader: FakeReader, socket: FakeSocket) -> ClamAvArtifactScanner:
    return ClamAvArtifactScanner(
        reader=reader,
        settings=ClamAvSettings("clamav:3310", 5, 90, 100),
        socket_factory=FakeSocketFactory(socket),
    )


def test_scanner_streams_bytes_to_clamd_and_returns_clean_without_content_in_result() -> None:
    reader = FakeReader(b"router config\n")
    socket = FakeSocket(b"stream: OK\0")

    result = scanner(reader, socket).scan(completed_job())

    assert result.disposition is ScanDisposition.CLEAN
    assert result.reason_code == "scan.clean"
    assert bytes(socket.sent).startswith(b"zINSTREAM\0")
    assert b"router config\n" in bytes(socket.sent)
    assert "router" not in repr(result)


def test_scanner_maps_malware_to_a_safe_rejection_without_daemon_text() -> None:
    result = scanner(FakeReader(b"x"), FakeSocket(b"stream: Eicar-Test-Signature FOUND\0")).scan(
        completed_job()
    )

    assert result.disposition is ScanDisposition.REJECTED
    assert result.reason_code == "scan.malware_detected"


def test_scanner_fails_closed_when_clamd_has_no_valid_response() -> None:
    result = scanner(FakeReader(b"x"), FakeSocket(b"stream: ERROR\0")).scan(completed_job())

    assert result.disposition is ScanDisposition.UNAVAILABLE
    assert result.reason_code == "scan.unavailable"


def test_scanner_rejects_an_oversized_stream_before_sending_its_chunk() -> None:
    socket = FakeSocket(b"stream: OK\0")
    result = scanner(FakeReader(b"x" * 101), socket).scan(completed_job())

    assert result.disposition is ScanDisposition.REJECTED
    assert result.reason_code == "scan.size_limit_exceeded"
    assert bytes(socket.sent) == b"zINSTREAM\0"
