"""Errors raised when a case transition violates domain invariants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from netops_api.domain.cases import CaseState


class DomainError(Exception):
    """Base class for errors that callers can map to a stable API response."""


@dataclass(slots=True)
class VersionConflictError(DomainError):
    """Raised when a command was composed from an outdated case projection."""

    case_id: UUID
    expected_version: int
    actual_version: int

    def __str__(self) -> str:
        return (
            f"Case {self.case_id} expected version {self.expected_version}, "
            f"but is at version {self.actual_version}."
        )


@dataclass(slots=True)
class CaseIdentityMismatchError(DomainError):
    """Raised when a transition command is applied to a different case."""

    snapshot_case_id: UUID
    command_case_id: UUID

    def __str__(self) -> str:
        return "The transition command does not belong to the supplied case."


@dataclass(slots=True)
class InvalidTransitionError(DomainError):
    """Raised when a state edge is absent from the case state machine."""

    from_state: CaseState
    to_state: CaseState

    def __str__(self) -> str:
        return f"A case cannot transition from {self.from_state} to {self.to_state}."


@dataclass(slots=True)
class TransitionAuthorizationError(DomainError):
    """Raised when an actor kind or role lacks authority for a state change."""

    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class TransitionConstraintError(DomainError):
    """Raised when a state-specific evidence or workflow invariant is missing."""

    message: str

    def __str__(self) -> str:
        return self.message
