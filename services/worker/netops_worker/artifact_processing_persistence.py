"""Worker adapter that records scanner verdicts in the tenant artifact ledger.

The adapter deliberately receives the opaque processing job/result types.  It
does not receive a private object reader, scanner output, or exception object,
and persists only the closed reason code supplied by ``ScanResult``.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection

from netops_api.application.artifact_processing import (
    ArtifactScanDisposition,
    RecordArtifactScanOutcome,
    RecordedArtifactScanOutcome,
    TenantArtifactProcessingRepository,
)
from netops_worker.artifact_processing import ArtifactProcessingJob, ArtifactProcessingResult

Clock = Callable[[], datetime]


class TenantConnectionSource(Protocol):
    """RLS-safe connection source; workers must never use an unscoped connection."""

    def tenant_connection(self, organization_id: UUID) -> AbstractContextManager[Connection]: ...


class ArtifactProcessingLedger(Protocol):
    """Small persistence port allowing deterministic worker orchestration tests."""

    def record_scan_outcome(
        self, command: RecordArtifactScanOutcome
    ) -> RecordedArtifactScanOutcome: ...


LedgerFactory = Callable[[Connection, UUID], ArtifactProcessingLedger]


class ArtifactProcessingPersistence:
    """Append a scanner outcome only for the job's verified tenant and artifact."""

    def __init__(
        self,
        database: TenantConnectionSource,
        *,
        processor: str,
        processor_version: str,
        ledger_factory: LedgerFactory = TenantArtifactProcessingRepository,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._processor = processor
        self._processor_version = processor_version
        self._ledger_factory = ledger_factory
        self._clock = clock or _utc_now

    def record(
        self, job: ArtifactProcessingJob, result: ArtifactProcessingResult
    ) -> RecordedArtifactScanOutcome:
        """Persist the bounded scanner verdict, never raw data or exception text."""
        if result.artifact_id != job.artifact_id:
            raise ValueError("Processing result artifact does not match its job.")
        occurred_at = self._clock()
        if occurred_at.tzinfo is not UTC or occurred_at.utcoffset() != UTC.utcoffset(occurred_at):
            raise ValueError("clock must return a timezone-aware UTC datetime.")
        with self._database.tenant_connection(job.organization_id) as connection:
            return self._ledger_factory(connection, job.organization_id).record_scan_outcome(
                RecordArtifactScanOutcome(
                    artifact_id=job.artifact_id,
                    disposition=ArtifactScanDisposition(result.scan.disposition.value),
                    reason_code=result.scan.reason_code,
                    processor=self._processor,
                    processor_version=self._processor_version,
                    occurred_at=occurred_at,
                )
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)
