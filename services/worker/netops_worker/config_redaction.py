"""Private, fail-closed Cisco configuration redaction derivative boundary.

Raw configuration is read only from a private reader and streamed directly to a
private writer.  The worker returns and records only bounded derivative
metadata: it must not log, persist, or place source bytes on a job/result.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import BinaryIO, Protocol
from uuid import UUID

from netops_api.application.config_preview import CONFIG_REDACTION_VERSION
from netops_api.ingestion.redaction import RedactionReport, redact_cisco_config
from netops_worker.artifact_processing import (
    ArtifactClassification,
    ArtifactKind,
    ArtifactProcessingJob,
    RedactionResult,
)

_DEFAULT_MAX_CONFIG_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class RedactedDerivativeMetadata:
    """Safe, immutable facts about one stored redacted derivative."""

    derivative_artifact_id: UUID
    derivative_sha256_hex: str
    byte_size: int
    redaction_version: str
    report: RedactionReport

    def __post_init__(self) -> None:
        if len(self.derivative_sha256_hex) != 64 or any(
            character not in "0123456789abcdef" for character in self.derivative_sha256_hex
        ):
            raise ValueError("derivative_sha256_hex must be a lowercase SHA-256 digest.")
        if self.byte_size < 1:
            raise ValueError("byte_size must be positive.")
        if self.redaction_version != CONFIG_REDACTION_VERSION:
            raise ValueError("redaction_version is not supported.")


class PrivateConfigArtifactReader(Protocol):
    """Open eligible source bytes without disclosing an object locator."""

    def open_for_redaction(self, job: ArtifactProcessingJob) -> BinaryIO:
        """Return a source stream owned by the redaction call."""


class PrivateRedactedDerivativeWriter(Protocol):
    """Store a derivative and its safe metadata in the private evidence boundary."""

    def write_redacted_config(
        self,
        job: ArtifactProcessingJob,
        *,
        derivative: BinaryIO,
        derivative_sha256_hex: str,
        byte_size: int,
        redaction_version: str,
        report: RedactionReport,
    ) -> RedactedDerivativeMetadata:
        """Write bytes privately; return metadata only after durable persistence."""


class ConfigDerivativeRedactor:
    """Create one redacted config derivative after :class:`ArtifactProcessor` marks clean.

    The caller is required to invoke this only behind ``ArtifactProcessor``'s
    clean scan gate.  This adapter additionally rejects every non-raw,
    non-configuration job so it cannot accidentally become a generic artifact
    transformer.
    """

    def __init__(
        self,
        *,
        reader: PrivateConfigArtifactReader,
        writer: PrivateRedactedDerivativeWriter,
        max_source_bytes: int = _DEFAULT_MAX_CONFIG_BYTES,
    ) -> None:
        if max_source_bytes < 1:
            raise ValueError("max_source_bytes must be positive.")
        self._reader = reader
        self._writer = writer
        self._max_source_bytes = max_source_bytes

    def redact(self, job: ArtifactProcessingJob) -> RedactionResult:
        """Read, redact, and write privately; return no raw source or derivative text."""
        _validate_eligible_config(job)
        source = self._read_bounded_source(job)
        try:
            decoded = source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("configuration encoding is not valid UTF-8.") from exc

        redaction = redact_cisco_config(decoded)
        derivative_bytes = redaction.content.encode("utf-8")
        if not derivative_bytes:
            raise ValueError("redacted configuration must not be empty.")
        derivative_sha256_hex = sha256(derivative_bytes).hexdigest()
        stored = self._writer.write_redacted_config(
            job,
            derivative=BytesIO(derivative_bytes),
            derivative_sha256_hex=derivative_sha256_hex,
            byte_size=len(derivative_bytes),
            redaction_version=CONFIG_REDACTION_VERSION,
            report=redaction.report,
        )
        if (
            stored.derivative_sha256_hex != derivative_sha256_hex
            or stored.byte_size != len(derivative_bytes)
            or stored.redaction_version != CONFIG_REDACTION_VERSION
        ):
            raise RuntimeError("private derivative writer returned inconsistent metadata.")
        return RedactionResult(
            derivative_sha256_hex=stored.derivative_sha256_hex,
            redaction_version=stored.redaction_version,
        )

    def _read_bounded_source(self, job: ArtifactProcessingJob) -> bytes:
        try:
            with self._reader.open_for_redaction(job) as source:
                value = source.read(self._max_source_bytes + 1)
        except OSError as exc:
            raise RuntimeError("private source artifact is unavailable.") from exc
        if len(value) > self._max_source_bytes:
            raise ValueError("configuration exceeds the redaction size limit.")
        if not value:
            raise ValueError("configuration must not be empty.")
        return value


def _validate_eligible_config(job: ArtifactProcessingJob) -> None:
    if (
        job.artifact_kind is not ArtifactKind.NETWORK_CONFIGURATION
        or job.classification is not ArtifactClassification.RAW
    ):
        raise ValueError("only raw network-configuration artifacts may be redacted.")
