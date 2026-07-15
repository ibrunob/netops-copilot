"""Transport-independent, evidence-bearing output for deterministic validators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from netops_api.parsers.cisco_ios.models import LifetimeUnit


class ValidatorStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    NOT_APPLICABLE = "not_applicable"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    """An immutable evidence location; source text stays in the stored artifact."""

    document: str
    line_number: int
    role: str


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    """A reproducible rule result with exact source-location evidence."""

    rule_id: str
    rule_version: str
    status: ValidatorStatus
    severity: FindingSeverity
    association_id: str | None
    unit: LifetimeUnit
    observed_value: int | None
    expected_value: int | None
    evidence: tuple[EvidenceLocation, ...]
    explanation: str
