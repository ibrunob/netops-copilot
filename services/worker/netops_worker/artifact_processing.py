"""Safe worker seam for processing completed private artifacts.

This module deliberately receives *only* immutable identifiers and declared
metadata from a completion event.  It never receives an object key, filename,
presigned URL, artifact bytes, or arbitrary exception text.  Production scanner
and redactor adapters can be introduced behind these ports once their sandbox,
egress, and retention contracts are approved; the included fakes make the job
orchestration testable without reaching a store or external service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from netops_api.application.outbox import LeasedOutboxEvent

ARTIFACT_COMPLETED_EVENT_TYPE = "artifact.completed.v1"


class ArtifactKind(StrEnum):
    """Artifact kinds currently admitted by the upload-completion boundary."""

    NETWORK_CONFIGURATION = "network-configuration"
    INCIDENT_AUDIO = "incident-audio"


class ArtifactClassification(StrEnum):
    """Classifications the worker may handle without changing access scope."""

    RAW = "raw"
    REDACTED = "redacted"
    DERIVED = "derived"


class ScanDisposition(StrEnum):
    """Safe, small scanner outcomes; never attach scanner output or source data."""

    CLEAN = "clean"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ArtifactCompletionEvent:
    """Typed completion message emitted only after object metadata verification.

    This is intentionally not a generic outbox payload parser.  A future
    transport must translate the durable completion fact into this model before
    it reaches processing, preventing a worker from accidentally processing
    pending uploads or arbitrary event payloads.
    """

    organization_id: UUID
    case_id: UUID
    artifact_id: UUID
    artifact_kind: ArtifactKind
    classification: ArtifactClassification
    sha256_hex: str

    def __post_init__(self) -> None:
        _validate_sha256(self.sha256_hex)


def artifact_job_from_outbox_event(event: LeasedOutboxEvent) -> ArtifactProcessingJob | None:
    """Translate one durable completion fact into an opaque processing job.

    Other event families are deliberately ignored here.  Artifact events have a
    closed payload schema so a producer cannot accidentally pass an object
    locator, source bytes, filename, URL, or arbitrary text to processing.
    """
    if event.event_type != ARTIFACT_COMPLETED_EVENT_TYPE:
        return None
    payload = event.payload
    expected_keys = {"artifact_id", "artifact_kind", "case_id", "classification", "sha256"}
    if set(payload) != expected_keys:
        raise ValueError("artifact.completed.v1 payload does not match its safe schema.")
    try:
        case_id = UUID(_string_payload(payload, "case_id"))
        if case_id != event.case_id:
            raise ValueError("artifact.completed.v1 case_id does not match its envelope.")
        completion = ArtifactCompletionEvent(
            organization_id=event.organization_id,
            case_id=case_id,
            artifact_id=UUID(_string_payload(payload, "artifact_id")),
            artifact_kind=ArtifactKind(_string_payload(payload, "artifact_kind")),
            classification=ArtifactClassification(_string_payload(payload, "classification")),
            sha256_hex=_string_payload(payload, "sha256"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact.completed.v1 payload is invalid.") from exc
    return ArtifactProcessingJob.from_completion_event(completion)


def _string_payload(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"artifact completion payload field {key} must be a string.")
    return value


@dataclass(frozen=True, slots=True)
class ArtifactProcessingJob:
    """Opaque, idempotency-friendly work item constructed from a completion fact."""

    organization_id: UUID
    case_id: UUID
    artifact_id: UUID
    artifact_kind: ArtifactKind
    classification: ArtifactClassification
    sha256_hex: str

    @classmethod
    def from_completion_event(cls, event: ArtifactCompletionEvent) -> ArtifactProcessingJob:
        return cls(
            organization_id=event.organization_id,
            case_id=event.case_id,
            artifact_id=event.artifact_id,
            artifact_kind=event.artifact_kind,
            classification=event.classification,
            sha256_hex=event.sha256_hex,
        )

    def __post_init__(self) -> None:
        _validate_sha256(self.sha256_hex)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Bounded scanner verdict, with a stable code safe for metrics and audit facts."""

    disposition: ScanDisposition
    reason_code: str

    def __post_init__(self) -> None:
        _validate_safe_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """A redaction result deliberately contains no source or derived bytes."""

    derivative_sha256_hex: str
    redaction_version: str

    def __post_init__(self) -> None:
        _validate_sha256(self.derivative_sha256_hex)
        _validate_safe_code(self.redaction_version)


class ArtifactScanner(Protocol):
    """Trusted, sandboxed scanner port addressed solely by a completed job."""

    def scan(self, job: ArtifactProcessingJob) -> ScanResult:
        """Scan the private artifact without exposing its bytes to orchestration."""


class ArtifactRedactor(Protocol):
    """Trusted redactor port addressed solely by a clean completed job."""

    def redact(self, job: ArtifactProcessingJob) -> RedactionResult:
        """Create a private derivative without returning either artifact body."""


@dataclass(frozen=True, slots=True)
class ArtifactProcessingResult:
    """Safe terminal result for a single idempotent processing attempt."""

    artifact_id: UUID
    scan: ScanResult
    redaction: RedactionResult | None = None


class ArtifactProcessor:
    """Run scanner then redactor, never logging or returning raw artifact material."""

    def __init__(self, *, scanner: ArtifactScanner, redactor: ArtifactRedactor) -> None:
        self._scanner = scanner
        self._redactor = redactor

    def process(self, job: ArtifactProcessingJob) -> ArtifactProcessingResult:
        """Process only a completed job; redact only an explicitly clean artifact."""
        scan = self._scanner.scan(job)
        if scan.disposition is not ScanDisposition.CLEAN:
            return ArtifactProcessingResult(artifact_id=job.artifact_id, scan=scan)
        redaction = self._redactor.redact(job)
        return ArtifactProcessingResult(
            artifact_id=job.artifact_id,
            scan=scan,
            redaction=redaction,
        )


@dataclass(slots=True)
class FakeArtifactScanner:
    """Deterministic no-I/O scanner fake for unit and workflow tests."""

    result: ScanResult = field(
        default_factory=lambda: ScanResult(ScanDisposition.CLEAN, "scan.clean")
    )
    jobs: list[ArtifactProcessingJob] = field(default_factory=list)

    def scan(self, job: ArtifactProcessingJob) -> ScanResult:
        self.jobs.append(job)
        return self.result


@dataclass(slots=True)
class FakeArtifactRedactor:
    """Deterministic no-I/O redactor fake for unit and workflow tests."""

    result: RedactionResult
    jobs: list[ArtifactProcessingJob] = field(default_factory=list)

    def redact(self, job: ArtifactProcessingJob) -> RedactionResult:
        self.jobs.append(job)
        return self.result


def _validate_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("sha256_hex must be a lowercase SHA-256 digest.")


def _validate_safe_code(value: str) -> None:
    if (
        not 1 <= len(value) <= 128
        or value.strip() != value
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._:-" for character in value)
    ):
        raise ValueError("value must be a 1 to 128 character safe code.")
