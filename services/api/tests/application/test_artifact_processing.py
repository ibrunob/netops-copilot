"""Unit contracts for bounded, tenant-scoped scanner ledger persistence."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from json import loads
from uuid import UUID

import pytest

from netops_api.application.artifact_processing import (
    ArtifactProcessingStateConflictError,
    ArtifactScanDisposition,
    RecordArtifactScanOutcome,
    TenantArtifactProcessingRepository,
)

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000002")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 7, 16, tzinfo=UTC)


class Result:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    def mappings(self) -> Result:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self._row


class Savepoint(AbstractContextManager[None]):
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class Connection:
    def __init__(self, active_row: dict[str, object] | None) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._active_row = active_row

    def begin_nested(self) -> Savepoint:
        return Savepoint()

    def execute(self, statement: object, parameters: dict[str, object]) -> Result:
        self.executed.append((str(statement), parameters))
        return Result(self._active_row if len(self.executed) == 1 else None)


def command(disposition: ArtifactScanDisposition) -> RecordArtifactScanOutcome:
    return RecordArtifactScanOutcome(
        artifact_id=ARTIFACT_ID,
        disposition=disposition,
        reason_code=(
            "scan.clean" if disposition is ArtifactScanDisposition.CLEAN else "scan.rejected"
        ),
        processor="clamav",
        processor_version="1.4.2",
        occurred_at=NOW,
    )


def test_clean_scan_appends_verified_metadata_only_for_active_quarantine() -> None:
    connection = Connection(
        {"attempt": 4, "state": "quarantined", "correlation_id": CORRELATION_ID}
    )

    recorded = TenantArtifactProcessingRepository(connection, ORGANIZATION_ID).record_scan_outcome(  # type: ignore[arg-type]
        command(ArtifactScanDisposition.CLEAN)
    )

    assert recorded.attempt == 4
    assert recorded.state == "verified"
    parameters = connection.executed[1][1]
    assert parameters["organization_id"] == ORGANIZATION_ID
    assert parameters["attempt"] == 4
    assert parameters["state"] == "verified"
    assert parameters["failure_code"] is None
    assert parameters["correlation_id"] == CORRELATION_ID
    assert loads(str(parameters["result_summary"])) == {"scanner_checks_completed": 1}
    serialized = str(parameters)
    assert "object-key" not in serialized
    assert "filename" not in serialized
    assert "exception" not in serialized


def test_non_clean_scan_appends_failed_with_bounded_code() -> None:
    connection = Connection(
        {"attempt": 1, "state": "quarantined", "correlation_id": CORRELATION_ID}
    )

    TenantArtifactProcessingRepository(connection, ORGANIZATION_ID).record_scan_outcome(  # type: ignore[arg-type]
        command(ArtifactScanDisposition.REJECTED)
    )

    parameters = connection.executed[1][1]
    assert parameters["state"] == "failed"
    assert parameters["failure_code"] == "scan.rejected"


@pytest.mark.parametrize("state", ["verified", "failed", "redacted"])
def test_replay_or_wrong_prior_state_fails_closed(state: str) -> None:
    connection = Connection({"attempt": 1, "state": state, "correlation_id": CORRELATION_ID})

    with pytest.raises(ArtifactProcessingStateConflictError):
        TenantArtifactProcessingRepository(connection, ORGANIZATION_ID).record_scan_outcome(  # type: ignore[arg-type]
            command(ArtifactScanDisposition.CLEAN)
        )

    assert len(connection.executed) == 1


def test_outcome_rejects_non_safe_or_raw_like_codes() -> None:
    with pytest.raises(ValueError):
        RecordArtifactScanOutcome(
            artifact_id=ARTIFACT_ID,
            disposition=ArtifactScanDisposition.CLEAN,
            reason_code="scanner leaked: running-config username secret",
            processor="clamav",
            processor_version="1",
            occurred_at=NOW,
        )
