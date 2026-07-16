from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from netops_api.application.outbox import LeasedOutboxEvent
from netops_worker.artifact_processing import (
    ArtifactClassification,
    ArtifactCompletionEvent,
    ArtifactKind,
    ArtifactProcessingJob,
    ArtifactProcessor,
    FakeArtifactRedactor,
    FakeArtifactScanner,
    RedactionResult,
    ScanDisposition,
    ScanResult,
    artifact_job_from_outbox_event,
)

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000002")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000003")
DIGEST = "a" * 64
DERIVATIVE_DIGEST = "b" * 64


def completion_outbox_event(*, payload: dict[str, object] | None = None) -> LeasedOutboxEvent:
    now = datetime(2026, 7, 16, tzinfo=UTC)
    return LeasedOutboxEvent(
        outbox_id=UUID("00000000-0000-0000-0000-000000000004"),
        organization_id=ORGANIZATION_ID,
        case_id=CASE_ID,
        case_event_id=UUID("00000000-0000-0000-0000-000000000005"),
        event_type="artifact.completed.v1",
        aggregate_version=0,
        correlation_id=UUID("00000000-0000-0000-0000-000000000006"),
        payload=payload
        or {
            "artifact_id": str(ARTIFACT_ID),
            "artifact_kind": "network-configuration",
            "case_id": str(CASE_ID),
            "classification": "raw",
            "sha256": DIGEST,
        },
        available_at=now,
        created_at=now,
        attempt_count=1,
        locked_at=now,
        locked_by="test-worker:attempt",
    )


def completed_job() -> ArtifactProcessingJob:
    return ArtifactProcessingJob.from_completion_event(
        ArtifactCompletionEvent(
            organization_id=ORGANIZATION_ID,
            case_id=CASE_ID,
            artifact_id=ARTIFACT_ID,
            artifact_kind=ArtifactKind.NETWORK_CONFIGURATION,
            classification=ArtifactClassification.RAW,
            sha256_hex=DIGEST,
        )
    )


def redactor() -> FakeArtifactRedactor:
    return FakeArtifactRedactor(
        RedactionResult(
            derivative_sha256_hex=DERIVATIVE_DIGEST,
            redaction_version="cisco-redaction-v1",
        )
    )


def test_processor_only_redacts_clean_completed_artifacts() -> None:
    job = completed_job()
    scanner = FakeArtifactScanner()
    fake_redactor = redactor()

    result = ArtifactProcessor(scanner=scanner, redactor=fake_redactor).process(job)

    assert result.artifact_id == ARTIFACT_ID
    assert result.scan == ScanResult(ScanDisposition.CLEAN, "scan.clean")
    assert result.redaction == fake_redactor.result
    assert scanner.jobs == [job]
    assert fake_redactor.jobs == [job]


@pytest.mark.parametrize("disposition", [ScanDisposition.REJECTED, ScanDisposition.UNAVAILABLE])
def test_processor_never_redacts_non_clean_artifacts(disposition: ScanDisposition) -> None:
    job = completed_job()
    scanner = FakeArtifactScanner(ScanResult(disposition, "scan.not_clean"))
    fake_redactor = redactor()

    result = ArtifactProcessor(scanner=scanner, redactor=fake_redactor).process(job)

    assert result.scan.disposition is disposition
    assert result.redaction is None
    assert fake_redactor.jobs == []


def test_processing_job_contains_only_completion_metadata() -> None:
    job = completed_job()

    assert set(job.__dataclass_fields__) == {
        "organization_id",
        "case_id",
        "artifact_id",
        "artifact_kind",
        "classification",
        "sha256_hex",
    }
    assert "storage" not in repr(job).lower()
    assert "content" not in repr(job).lower()


def test_artifact_completion_outbox_event_becomes_opaque_processing_job() -> None:
    assert artifact_job_from_outbox_event(completion_outbox_event()) == completed_job()


def test_non_artifact_outbox_event_is_not_a_processing_job() -> None:
    event = replace(completion_outbox_event(), event_type="case.created.v1")
    assert artifact_job_from_outbox_event(event) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"artifact_id": str(ARTIFACT_ID)},
        {
            "artifact_id": str(ARTIFACT_ID),
            "artifact_kind": "network-configuration",
            "case_id": str(CASE_ID),
            "classification": "raw",
            "sha256": DIGEST,
            "storage_key": "private/source-config",
        },
        {
            "artifact_id": str(ARTIFACT_ID),
            "artifact_kind": "network-configuration",
            "case_id": str(ARTIFACT_ID),
            "classification": "raw",
            "sha256": DIGEST,
        },
    ],
)
def test_artifact_completion_parser_rejects_untrusted_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="artifact.completed"):
        artifact_job_from_outbox_event(completion_outbox_event(payload=payload))


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "a" * 63 + "!"])
def test_completion_job_rejects_noncanonical_digests(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ArtifactCompletionEvent(
            organization_id=ORGANIZATION_ID,
            case_id=CASE_ID,
            artifact_id=ARTIFACT_ID,
            artifact_kind=ArtifactKind.NETWORK_CONFIGURATION,
            classification=ArtifactClassification.RAW,
            sha256_hex=digest,
        )


@pytest.mark.parametrize("value", ["scan message with spaces", "secret=value", ""])
def test_safe_result_codes_reject_arbitrary_text(value: str) -> None:
    with pytest.raises(ValueError, match="safe code"):
        ScanResult(ScanDisposition.REJECTED, value)
