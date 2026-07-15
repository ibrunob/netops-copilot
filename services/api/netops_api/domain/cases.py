"""Pure, evidence-aware state transitions for the mutable case projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from netops_api.domain.errors import (
    CaseIdentityMismatchError,
    InvalidTransitionError,
    TransitionAuthorizationError,
    TransitionConstraintError,
    VersionConflictError,
)


class CaseState(StrEnum):
    """States of the case projection, in the order they can normally progress."""

    NEW = "new"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    FIX_PROPOSED = "fix_proposed"
    NEEDS_INFORMATION = "needs_information"
    CONFIRMED = "confirmed"
    RESOLVED = "resolved"
    LEARNED = "learned"


class CaseRole(StrEnum):
    """Organization roles defined by the product authorization boundary."""

    ORG_ADMIN = "org_admin"
    OPERATOR = "operator"
    APPROVER = "approver"
    AUDITOR = "auditor"
    INTEGRATION_ADMIN = "integration_admin"
    PLATFORM_ADMIN = "platform_admin"


class ActorKind(StrEnum):
    """Origin of a command; models are advisory and cannot mutate case state."""

    HUMAN = "human"
    SERVICE = "service"
    MODEL = "model"


ALLOWED_TRANSITIONS: Final[dict[CaseState, frozenset[CaseState]]] = {
    CaseState.NEW: frozenset({CaseState.INVESTIGATING}),
    CaseState.INVESTIGATING: frozenset({CaseState.DIAGNOSED, CaseState.NEEDS_INFORMATION}),
    CaseState.NEEDS_INFORMATION: frozenset({CaseState.INVESTIGATING}),
    CaseState.DIAGNOSED: frozenset({CaseState.FIX_PROPOSED}),
    CaseState.FIX_PROPOSED: frozenset({CaseState.CONFIRMED}),
    CaseState.CONFIRMED: frozenset({CaseState.RESOLVED}),
    CaseState.RESOLVED: frozenset({CaseState.LEARNED}),
    CaseState.LEARNED: frozenset(),
}

_CASE_WRITE_ROLES: Final[frozenset[CaseRole]] = frozenset(
    {
        CaseRole.ORG_ADMIN,
        CaseRole.OPERATOR,
        CaseRole.APPROVER,
        CaseRole.PLATFORM_ADMIN,
    }
)
_CONFIRM_ROLES: Final[frozenset[CaseRole]] = frozenset(
    {CaseRole.ORG_ADMIN, CaseRole.APPROVER, CaseRole.PLATFORM_ADMIN}
)
_RESOLVE_ROLES: Final[frozenset[CaseRole]] = frozenset(
    {CaseRole.ORG_ADMIN, CaseRole.OPERATOR, CaseRole.APPROVER, CaseRole.PLATFORM_ADMIN}
)

_EVENT_TYPES: Final[dict[CaseState, str]] = {
    CaseState.INVESTIGATING: "case.investigating.v1",
    CaseState.DIAGNOSED: "analysis.completed.v1",
    CaseState.FIX_PROPOSED: "recommendation.proposed.v1",
    CaseState.NEEDS_INFORMATION: "case.needs_information.v1",
    CaseState.CONFIRMED: "approval.granted.v1",
    CaseState.RESOLVED: "case.resolved.v1",
    CaseState.LEARNED: "memory.indexed.v1",
    CaseState.NEW: "case.created.v1",
}


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated human or workload that originated a state command."""

    actor_id: UUID
    kind: ActorKind
    roles: frozenset[CaseRole] = frozenset()

    def __post_init__(self) -> None:
        if self.kind is ActorKind.HUMAN and not self.roles:
            raise ValueError("Human actors must include at least one assigned role.")
        if self.kind is not ActorKind.HUMAN and self.roles:
            raise ValueError("Only human actors may carry organization roles.")


@dataclass(frozen=True, slots=True)
class CaseSnapshot:
    """The current materialized case state needed to decide a transition."""

    case_id: UUID
    state: CaseState
    version: int

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ValueError("Case version cannot be negative.")


@dataclass(frozen=True, slots=True)
class TransitionCommand:
    """Immutable transition intent supplied by an API, worker, or human action."""

    transition_id: UUID
    event_id: UUID
    case_id: UUID
    expected_version: int
    to_state: CaseState
    actor: Actor
    correlation_id: UUID
    occurred_at: datetime
    approval_id: UUID | None = None
    verification_note: str | None = None
    knowledge_item_id: UUID | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.expected_version < 0:
            raise ValueError("Expected version cannot be negative.")
        _require_utc(self.occurred_at)
        for field_name in ("verification_note", "note"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} cannot be blank when supplied.")


@dataclass(frozen=True, slots=True)
class CaseStateTransition:
    """Immutable transition record to persist with the next case projection version."""

    transition_id: UUID
    case_id: UUID
    from_state: CaseState
    to_state: CaseState
    version: int
    actor_id: UUID
    correlation_id: UUID
    occurred_at: datetime
    approval_id: UUID | None
    verification_note: str | None
    knowledge_item_id: UUID | None
    note: str | None


@dataclass(frozen=True, slots=True)
class CaseEventCommand:
    """Immutable command for the transactionally persisted case event/outbox row."""

    event_id: UUID
    event_type: str
    case_id: UUID
    aggregate_version: int
    transition_id: UUID
    actor_id: UUID
    correlation_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    """All immutable records produced by a successful pure transition decision."""

    snapshot: CaseSnapshot
    transition: CaseStateTransition
    event: CaseEventCommand


def apply_transition(snapshot: CaseSnapshot, command: TransitionCommand) -> TransitionOutcome:
    """Validate and apply one state command without I/O or mutable side effects."""
    if snapshot.case_id != command.case_id:
        raise CaseIdentityMismatchError(
            snapshot_case_id=snapshot.case_id,
            command_case_id=command.case_id,
        )
    if snapshot.version != command.expected_version:
        raise VersionConflictError(
            case_id=snapshot.case_id,
            expected_version=command.expected_version,
            actual_version=snapshot.version,
        )
    if command.to_state not in ALLOWED_TRANSITIONS[snapshot.state]:
        raise InvalidTransitionError(from_state=snapshot.state, to_state=command.to_state)

    _validate_actor(command)
    _validate_transition_constraints(command)

    next_version = snapshot.version + 1
    next_snapshot = CaseSnapshot(
        case_id=snapshot.case_id,
        state=command.to_state,
        version=next_version,
    )
    transition = CaseStateTransition(
        transition_id=command.transition_id,
        case_id=snapshot.case_id,
        from_state=snapshot.state,
        to_state=command.to_state,
        version=next_version,
        actor_id=command.actor.actor_id,
        correlation_id=command.correlation_id,
        occurred_at=command.occurred_at,
        approval_id=command.approval_id,
        verification_note=command.verification_note,
        knowledge_item_id=command.knowledge_item_id,
        note=command.note,
    )
    event = CaseEventCommand(
        event_id=command.event_id,
        event_type=_EVENT_TYPES[command.to_state],
        case_id=snapshot.case_id,
        aggregate_version=next_version,
        transition_id=command.transition_id,
        actor_id=command.actor.actor_id,
        correlation_id=command.correlation_id,
        occurred_at=command.occurred_at,
    )
    return TransitionOutcome(snapshot=next_snapshot, transition=transition, event=event)


def _validate_actor(command: TransitionCommand) -> None:
    actor = command.actor
    if actor.kind is ActorKind.MODEL:
        raise TransitionAuthorizationError("Model identities cannot alter case state.")

    if command.to_state in {CaseState.CONFIRMED, CaseState.RESOLVED}:
        if actor.kind is not ActorKind.HUMAN:
            raise TransitionAuthorizationError(
                f"Transitioning to {command.to_state} requires an authorized human actor."
            )

    if command.to_state is CaseState.CONFIRMED and not actor.roles.intersection(_CONFIRM_ROLES):
        raise TransitionAuthorizationError("The actor lacks an approver role for confirmation.")
    if command.to_state is CaseState.RESOLVED and not actor.roles.intersection(_RESOLVE_ROLES):
        raise TransitionAuthorizationError("The actor lacks a case-resolution role.")

    if actor.kind is ActorKind.HUMAN and not actor.roles.intersection(_CASE_WRITE_ROLES):
        raise TransitionAuthorizationError("The actor has no role that can mutate a case.")

    if command.to_state is CaseState.LEARNED and actor.kind is not ActorKind.SERVICE:
        raise TransitionAuthorizationError(
            "Transitioning to learned requires the indexing workflow service identity."
        )


def _validate_transition_constraints(command: TransitionCommand) -> None:
    if command.to_state is CaseState.CONFIRMED and command.approval_id is None:
        raise TransitionConstraintError("Confirmation requires an immutable approval record ID.")
    if command.to_state is CaseState.RESOLVED and command.verification_note is None:
        raise TransitionConstraintError("Resolution requires a non-empty human verification note.")
    if command.to_state is CaseState.LEARNED and command.knowledge_item_id is None:
        raise TransitionConstraintError("Learning requires an indexed knowledge item ID.")
    if command.to_state is CaseState.NEEDS_INFORMATION and command.note is None:
        raise TransitionConstraintError(
            "Needs-information transitions require an operator-readable note."
        )


def _require_utc(value: datetime) -> None:
    if value.tzinfo is not UTC or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("occurred_at must be timezone-aware UTC.")
