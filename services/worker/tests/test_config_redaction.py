from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from uuid import UUID

import pytest

from netops_api.application.config_preview import CONFIG_REDACTION_VERSION
from netops_api.ingestion.redaction import RedactionReport
from netops_worker.artifact_processing import (
    ArtifactClassification,
    ArtifactKind,
    ArtifactProcessingJob,
    ArtifactProcessor,
    FakeArtifactScanner,
    ScanDisposition,
    ScanResult,
)
from netops_worker.config_redaction import (
    ConfigDerivativeRedactor,
    RedactedDerivativeMetadata,
)

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000002")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000003")
DERIVATIVE_ID = UUID("00000000-0000-0000-0000-000000000004")


def config_job() -> ArtifactProcessingJob:
    return ArtifactProcessingJob(
        organization_id=ORGANIZATION_ID,
        case_id=CASE_ID,
        artifact_id=ARTIFACT_ID,
        artifact_kind=ArtifactKind.NETWORK_CONFIGURATION,
        classification=ArtifactClassification.RAW,
        sha256_hex="a" * 64,
    )


@dataclass
class FakeReader:
    source: bytes
    jobs: list[ArtifactProcessingJob] = field(default_factory=list)

    def open_for_redaction(self, job: ArtifactProcessingJob) -> BytesIO:
        self.jobs.append(job)
        return BytesIO(self.source)


@dataclass
class FakeWriter:
    content: bytes | None = None
    jobs: list[ArtifactProcessingJob] = field(default_factory=list)
    reports: list[RedactionReport] = field(default_factory=list)

    def write_redacted_config(
        self,
        job: ArtifactProcessingJob,
        *,
        derivative: BytesIO,
        derivative_sha256_hex: str,
        byte_size: int,
        redaction_version: str,
        report: RedactionReport,
    ) -> RedactedDerivativeMetadata:
        self.jobs.append(job)
        self.content = derivative.read()
        self.reports.append(report)
        return RedactedDerivativeMetadata(
            derivative_artifact_id=DERIVATIVE_ID,
            derivative_sha256_hex=derivative_sha256_hex,
            byte_size=byte_size,
            redaction_version=redaction_version,
            report=report,
        )


def test_clean_config_is_redacted_and_stored_as_a_safe_derivative() -> None:
    reader = FakeReader(b"username netops secret Swordfish\ninterface Gi0/1\n")
    writer = FakeWriter()
    redactor = ConfigDerivativeRedactor(reader=reader, writer=writer)

    result = ArtifactProcessor(scanner=FakeArtifactScanner(), redactor=redactor).process(
        config_job()
    )

    assert result.scan == ScanResult(ScanDisposition.CLEAN, "scan.clean")
    assert result.redaction is not None
    assert result.redaction.redaction_version == CONFIG_REDACTION_VERSION
    assert writer.content == (
        b"username netops secret <redacted:cisco.username_secret>\ninterface Gi0/1\n"
    )
    assert b"Swordfish" not in writer.content
    assert writer.reports[0].redacted_line_count == 1
    assert reader.jobs == [config_job()]
    assert writer.jobs == [config_job()]
    assert "Swordfish" not in repr(result)


@pytest.mark.parametrize("disposition", [ScanDisposition.REJECTED, ScanDisposition.UNAVAILABLE])
def test_non_clean_artifact_never_reaches_private_reader(disposition: ScanDisposition) -> None:
    reader = FakeReader(b"username netops secret Swordfish\n")
    writer = FakeWriter()
    processor = ArtifactProcessor(
        scanner=FakeArtifactScanner(ScanResult(disposition, "scan.not_clean")),
        redactor=ConfigDerivativeRedactor(reader=reader, writer=writer),
    )

    result = processor.process(config_job())

    assert result.redaction is None
    assert reader.jobs == []
    assert writer.jobs == []


def test_redactor_rejects_non_config_artifacts_before_reading() -> None:
    reader = FakeReader(b"untrusted")
    writer = FakeWriter()
    job = ArtifactProcessingJob(
        organization_id=ORGANIZATION_ID,
        case_id=CASE_ID,
        artifact_id=ARTIFACT_ID,
        artifact_kind=ArtifactKind.INCIDENT_AUDIO,
        classification=ArtifactClassification.RAW,
        sha256_hex="a" * 64,
    )

    with pytest.raises(ValueError, match="only raw network"):
        ConfigDerivativeRedactor(reader=reader, writer=writer).redact(job)

    assert reader.jobs == []
    assert writer.jobs == []


def test_redactor_bounds_source_before_decoding_or_writing() -> None:
    reader = FakeReader(b"x" * 9)
    writer = FakeWriter()

    with pytest.raises(ValueError, match="size limit"):
        ConfigDerivativeRedactor(reader=reader, writer=writer, max_source_bytes=8).redact(
            config_job()
        )

    assert writer.jobs == []
