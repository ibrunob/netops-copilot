"""Worker contracts for tenant-scoped scanner outcome persistence."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from netops_api.application.artifact_processing import (
    ArtifactScanDisposition,
    RecordArtifactScanOutcome,
    RecordedArtifactScanOutcome,
)
from netops_worker.artifact_processing import (
    ArtifactClassification,
    ArtifactKind,
    ArtifactProcessingJob,
    ArtifactProcessingResult,
    ScanDisposition,
    ScanResult,
)
from netops_worker.artifact_processing_persistence import ArtifactProcessingPersistence

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000002")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


class Connection:
    pass


class Database:
    def __init__(self) -> None:
        self.organization_ids: list[UUID] = []

    def tenant_connection(self, organization_id: UUID) -> AbstractContextManager[Connection]:
        self.organization_ids.append(organization_id)
        return _ConnectionContext()


class _ConnectionContext(AbstractContextManager[Connection]):
    def __enter__(self) -> Connection:
        return Connection()

    def __exit__(self, *_: object) -> None:
        return None


class Ledger:
    def __init__(self) -> None:
        self.commands: list[RecordArtifactScanOutcome] = []

    def record_scan_outcome(
        self, command: RecordArtifactScanOutcome
    ) -> RecordedArtifactScanOutcome:
        self.commands.append(command)
        return RecordedArtifactScanOutcome(command.artifact_id, 1, "verified", command.occurred_at)


def job() -> ArtifactProcessingJob:
    return ArtifactProcessingJob(
        organization_id=ORGANIZATION_ID,
        case_id=CASE_ID,
        artifact_id=ARTIFACT_ID,
        artifact_kind=ArtifactKind.NETWORK_CONFIGURATION,
        classification=ArtifactClassification.RAW,
        sha256_hex="a" * 64,
    )


def test_worker_persists_only_closed_scanner_outcome_inside_job_tenant() -> None:
    database = Database()
    ledger = Ledger()
    persistence = ArtifactProcessingPersistence(
        database,  # type: ignore[arg-type]
        processor="clamav",
        processor_version="1.4.2",
        ledger_factory=lambda _connection, _organization_id: ledger,
        clock=lambda: NOW,
    )

    persistence.record(
        job(),
        ArtifactProcessingResult(ARTIFACT_ID, ScanResult(ScanDisposition.CLEAN, "scan.clean")),
    )

    assert database.organization_ids == [ORGANIZATION_ID]
    assert ledger.commands == [
        RecordArtifactScanOutcome(
            artifact_id=ARTIFACT_ID,
            disposition=ArtifactScanDisposition.CLEAN,
            reason_code="scan.clean",
            processor="clamav",
            processor_version="1.4.2",
            occurred_at=NOW,
        )
    ]


def test_worker_refuses_result_for_another_artifact_before_database_access() -> None:
    database = Database()
    persistence = ArtifactProcessingPersistence(
        database,  # type: ignore[arg-type]
        processor="clamav",
        processor_version="1",
    )
    other_artifact_id = UUID("00000000-0000-0000-0000-000000000004")

    with pytest.raises(ValueError, match="does not match"):
        persistence.record(
            job(),
            ArtifactProcessingResult(
                other_artifact_id, ScanResult(ScanDisposition.CLEAN, "scan.clean")
            ),
        )

    assert database.organization_ids == []
