"""Pure domain rules for NetOps Copilot."""

from netops_api.domain.cases import (
    ALLOWED_TRANSITIONS,
    Actor,
    ActorKind,
    CaseEventCommand,
    CaseRole,
    CaseSnapshot,
    CaseState,
    CaseStateTransition,
    TransitionCommand,
    TransitionOutcome,
    apply_transition,
)
from netops_api.domain.errors import (
    CaseIdentityMismatchError,
    DomainError,
    InvalidTransitionError,
    TransitionAuthorizationError,
    TransitionConstraintError,
    VersionConflictError,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "Actor",
    "ActorKind",
    "CaseEventCommand",
    "CaseIdentityMismatchError",
    "CaseRole",
    "CaseSnapshot",
    "CaseState",
    "CaseStateTransition",
    "DomainError",
    "InvalidTransitionError",
    "TransitionAuthorizationError",
    "TransitionCommand",
    "TransitionConstraintError",
    "TransitionOutcome",
    "VersionConflictError",
    "apply_transition",
]
