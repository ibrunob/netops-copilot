"""Safe, deterministic ingestion primitives with no external service dependencies."""

from netops_api.ingestion.redaction import (
    RedactionReport,
    RedactionResult,
    RedactionRule,
    RedactionRuleSummary,
    redact_cisco_config,
)

__all__ = [
    "RedactionReport",
    "RedactionResult",
    "RedactionRule",
    "RedactionRuleSummary",
    "redact_cisco_config",
]
