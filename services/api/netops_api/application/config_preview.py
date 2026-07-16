"""Safe, non-persistent configuration redaction previews.

This boundary deliberately accepts raw configuration only long enough to produce
a redacted derivative.  It has no repository, outbox, telemetry, or logging
dependencies: callers must never persist or emit the source text.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from netops_api.ingestion.redaction import RedactionReport, redact_cisco_config

CONFIG_PREVIEW_MAX_BYTES = 256 * 1024
CONFIG_PREVIEW_MAX_LINES = 10_000
CONFIG_REDACTION_VERSION = "cisco-redaction-v1"


class ConfigPreviewLimitError(ValueError):
    """A raw preview input exceeded a pre-redaction safety limit."""


@dataclass(frozen=True, slots=True)
class ConfigPreview:
    """The only data allowed to leave the raw-config preview boundary."""

    redacted_content: str
    redacted_content_sha256: str
    redaction_version: str
    report: RedactionReport


def preview_cisco_config(source: str) -> ConfigPreview:
    """Bound and redact one config paste without retaining the source text.

    Byte and line checks happen before redaction so unusually large submissions
    cannot consume unbounded CPU/memory.  The digest intentionally covers the
    redacted derivative, not the raw source, to avoid exposing a stable oracle
    for unredacted credentials.
    """
    source_bytes = source.encode("utf-8")
    if len(source_bytes) > CONFIG_PREVIEW_MAX_BYTES:
        raise ConfigPreviewLimitError("config input exceeds the byte limit")
    if len(source.splitlines()) > CONFIG_PREVIEW_MAX_LINES:
        raise ConfigPreviewLimitError("config input exceeds the line limit")

    redaction = redact_cisco_config(source)
    redacted_content = redaction.content
    return ConfigPreview(
        redacted_content=redacted_content,
        redacted_content_sha256=sha256(redacted_content.encode("utf-8")).hexdigest(),
        redaction_version=CONFIG_REDACTION_VERSION,
        report=redaction.report,
    )
