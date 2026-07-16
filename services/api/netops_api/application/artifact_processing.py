"""Append scanner verdicts to the immutable, tenant-scoped artifact ledger.

This repository is deliberately a *result* boundary.  It accepts only a
closed scanner disposition and stable reason code; object bytes, object keys,
filenames, scanner transcripts, and exception text cannot be persisted here.
The database trigger remains the final transition authority, while the query
below makes an out-of-date worker fail closed before attempting an insert.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Connection, text

_SAFE_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")


class ArtifactScanDisposition(StrEnum):
    """Closed scanner outcomes that are safe to retain in the ledger."""

    CLEAN = "clean"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class ArtifactProcessingStateConflictError(RuntimeError):
    """The worker result no longer matches the artifact's active attempt."""

    def __init__(self, artifact_id: UUID) -> None:
        super().__init__("Artifact does not have an active quarantined processing attempt.")
        self.artifact_id = artifact_id


@dataclass(frozen=True, slots=True)
class RecordArtifactScanOutcome:
    """Metadata-only scanner outcome to append for the current attempt."""

    artifact_id: UUID
    disposition: ArtifactScanDisposition
    reason_code: str
    processor: str
    processor_version: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("reason_code", self.reason_code),
            ("processor", self.processor),
            ("processor_version", self.processor_version),
        ):
            if not _SAFE_CODE.fullmatch(value):
                raise ValueError(f"{field_name} must be a 1 to 128 character safe code.")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class RecordedArtifactScanOutcome:
    """The non-sensitive immutable fact that was appended."""

    artifact_id: UUID
    attempt: int
    state: str
    occurred_at: datetime


class TenantArtifactProcessingRepository:
    """Persist one scanner verdict using a verified RLS tenant transaction."""

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def record_scan_outcome(
        self, command: RecordArtifactScanOutcome
    ) -> RecordedArtifactScanOutcome:
        """Append ``verified`` for clean scans, otherwise ``failed``.

        The selected row must be the latest row of the highest attempt and be
        quarantined.  Thus a replay after a result was recorded, or a result
        from an older attempt, cannot silently create a second transition.
        The ledger trigger additionally serializes the insert against another
        worker and validates the exact state transition.
        """
        occurred_at = command.occurred_at.astimezone(UTC)
        with self._atomic():
            active_attempt = (
                self._connection.execute(
                    _SELECT_ACTIVE_QUARANTINED_ATTEMPT,
                    {
                        "organization_id": self._organization_id,
                        "artifact_id": command.artifact_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if active_attempt is None or active_attempt["state"] != "quarantined":
                raise ArtifactProcessingStateConflictError(command.artifact_id)

            state = "verified" if command.disposition is ArtifactScanDisposition.CLEAN else "failed"
            failure_code = None if state == "verified" else command.reason_code
            # The detailed disposition is represented by immutable ``state``
            # and the bounded ``failure_code`` column.  Keep JSON strictly
            # count-only so it cannot become a home for scanner output.
            result_summary = '{"scanner_checks_completed":1}'
            self._connection.execute(
                _INSERT_SCAN_OUTCOME,
                {
                    "organization_id": self._organization_id,
                    "artifact_id": command.artifact_id,
                    "attempt": active_attempt["attempt"],
                    "state": state,
                    "processor": command.processor,
                    "processor_version": command.processor_version,
                    "failure_code": failure_code,
                    "result_summary": result_summary,
                    # Preserve the completion correlation ID without accepting
                    # an arbitrary correlation value from the worker message.
                    "correlation_id": active_attempt["correlation_id"],
                    "occurred_at": occurred_at,
                },
            )
        return RecordedArtifactScanOutcome(
            artifact_id=command.artifact_id,
            attempt=active_attempt["attempt"],
            state=state,
            occurred_at=occurred_at,
        )

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        with self._connection.begin_nested():
            yield


_SELECT_ACTIVE_QUARANTINED_ATTEMPT = text(
    """
    SELECT attempt, state, correlation_id
      FROM artifact_processing_events
     WHERE organization_id = :organization_id
       AND artifact_id = :artifact_id
     ORDER BY attempt DESC, occurred_at DESC, id DESC
     LIMIT 1
     FOR UPDATE
    """
)

_INSERT_SCAN_OUTCOME = text(
    """
    INSERT INTO artifact_processing_events (
      organization_id, artifact_id, attempt, state, processor, processor_version,
      failure_code, result_summary, correlation_id, occurred_at
    ) VALUES (
      :organization_id, :artifact_id, :attempt, :state, :processor, :processor_version,
      :failure_code, CAST(:result_summary AS jsonb), :correlation_id, :occurred_at
    )
    """
)
