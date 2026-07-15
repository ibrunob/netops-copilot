from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from itertools import product
from uuid import UUID, uuid4

import pytest

from netops_api.domain.cases import (
    ALLOWED_TRANSITIONS,
    Actor,
    ActorKind,
    CaseRole,
    CaseSnapshot,
    CaseState,
    TransitionCommand,
    apply_transition,
)
from netops_api.domain.errors import (
    CaseIdentityMismatchError,
    InvalidTransitionError,
    TransitionAuthorizationError,
    TransitionConstraintError,
    VersionConflictError,
)

CASE_ID = UUID("00000000-0000-0000-0000-000000000001")
TRANSITION_ID = UUID("00000000-0000-0000-0000-000000000002")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000003")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000004")
APPROVAL_ID = UUID("00000000-0000-0000-0000-000000000005")
KNOWLEDGE_ITEM_ID = UUID("00000000-0000-0000-0000-000000000006")
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def human(*roles: CaseRole) -> Actor:
    return Actor(actor_id=uuid4(), kind=ActorKind.HUMAN, roles=frozenset(roles))


def service() -> Actor:
    return Actor(actor_id=uuid4(), kind=ActorKind.SERVICE)


def model() -> Actor:
    return Actor(actor_id=uuid4(), kind=ActorKind.MODEL)


def command(
    snapshot: CaseSnapshot,
    to_state: CaseState,
    *,
    actor: Actor | None = None,
    expected_version: int | None = None,
    case_id: UUID | None = None,
    approval_id: UUID | None = None,
    verification_note: str | None = None,
    knowledge_item_id: UUID | None = None,
    note: str | None = None,
) -> TransitionCommand:
    return TransitionCommand(
        transition_id=TRANSITION_ID,
        event_id=EVENT_ID,
        case_id=case_id or snapshot.case_id,
        expected_version=snapshot.version if expected_version is None else expected_version,
        to_state=to_state,
        actor=actor or service(),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
        approval_id=approval_id,
        verification_note=verification_note,
        knowledge_item_id=knowledge_item_id,
        note=note,
    )


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (CaseState.NEW, CaseState.INVESTIGATING),
        (CaseState.INVESTIGATING, CaseState.DIAGNOSED),
        (CaseState.INVESTIGATING, CaseState.NEEDS_INFORMATION),
        (CaseState.NEEDS_INFORMATION, CaseState.INVESTIGATING),
        (CaseState.DIAGNOSED, CaseState.FIX_PROPOSED),
        (CaseState.FIX_PROPOSED, CaseState.CONFIRMED),
        (CaseState.CONFIRMED, CaseState.RESOLVED),
        (CaseState.RESOLVED, CaseState.LEARNED),
    ],
)
def test_all_allowed_state_edges_apply(from_state: CaseState, to_state: CaseState) -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=from_state, version=7)
    actor: Actor = service()
    kwargs: dict[str, object] = {}
    if to_state is CaseState.NEEDS_INFORMATION:
        kwargs["note"] = "Please attach peer tunnel evidence."
    if to_state is CaseState.CONFIRMED:
        actor = human(CaseRole.APPROVER)
        kwargs["approval_id"] = APPROVAL_ID
    if to_state is CaseState.RESOLVED:
        actor = human(CaseRole.OPERATOR)
        kwargs["verification_note"] = "Operator verified tunnel traffic recovery."
    if to_state is CaseState.LEARNED:
        kwargs["knowledge_item_id"] = KNOWLEDGE_ITEM_ID

    outcome = apply_transition(snapshot, command(snapshot, to_state, actor=actor, **kwargs))

    assert outcome.snapshot == CaseSnapshot(case_id=CASE_ID, state=to_state, version=8)
    assert outcome.transition.from_state is from_state
    assert outcome.transition.to_state is to_state
    assert outcome.transition.version == 8
    assert outcome.event.aggregate_version == 8
    assert outcome.event.transition_id == TRANSITION_ID


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (from_state, to_state)
        for from_state, to_state in product(CaseState, CaseState)
        if to_state not in ALLOWED_TRANSITIONS[from_state]
    ],
)
def test_every_undefined_state_edge_is_rejected(from_state: CaseState, to_state: CaseState) -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=from_state, version=0)

    with pytest.raises(InvalidTransitionError):
        apply_transition(snapshot, command(snapshot, to_state))


def test_stale_command_fails_before_mutating_the_projection() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.NEW, version=4)

    with pytest.raises(VersionConflictError) as error:
        apply_transition(
            snapshot,
            command(snapshot, CaseState.INVESTIGATING, expected_version=3),
        )

    assert error.value.expected_version == 3
    assert error.value.actual_version == 4
    assert snapshot.state is CaseState.NEW
    assert snapshot.version == 4


def test_command_for_another_case_is_rejected() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.NEW, version=0)

    with pytest.raises(CaseIdentityMismatchError):
        apply_transition(
            snapshot,
            command(snapshot, CaseState.INVESTIGATING, case_id=uuid4()),
        )


def test_confirmation_requires_authorized_human_and_approval_record() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.FIX_PROPOSED, version=3)

    with pytest.raises(TransitionAuthorizationError, match="authorized human"):
        apply_transition(
            snapshot,
            command(snapshot, CaseState.CONFIRMED, actor=service(), approval_id=APPROVAL_ID),
        )
    with pytest.raises(TransitionAuthorizationError, match="approver role"):
        apply_transition(
            snapshot,
            command(
                snapshot,
                CaseState.CONFIRMED,
                actor=human(CaseRole.OPERATOR),
                approval_id=APPROVAL_ID,
            ),
        )
    with pytest.raises(TransitionConstraintError, match="approval record"):
        apply_transition(
            snapshot,
            command(snapshot, CaseState.CONFIRMED, actor=human(CaseRole.APPROVER)),
        )


def test_resolution_requires_authorized_human_and_verification_note() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.CONFIRMED, version=5)

    with pytest.raises(TransitionAuthorizationError, match="authorized human"):
        apply_transition(
            snapshot,
            command(
                snapshot,
                CaseState.RESOLVED,
                actor=service(),
                verification_note="Verified by monitoring and operator.",
            ),
        )
    with pytest.raises(TransitionConstraintError, match="verification note"):
        apply_transition(
            snapshot,
            command(snapshot, CaseState.RESOLVED, actor=human(CaseRole.OPERATOR)),
        )


@pytest.mark.parametrize(
    ("from_state", "to_state", "kwargs"),
    [
        (CaseState.FIX_PROPOSED, CaseState.CONFIRMED, {"approval_id": APPROVAL_ID}),
        (
            CaseState.CONFIRMED,
            CaseState.RESOLVED,
            {"verification_note": "This must be a human action."},
        ),
        (CaseState.RESOLVED, CaseState.LEARNED, {"knowledge_item_id": KNOWLEDGE_ITEM_ID}),
    ],
)
def test_models_cannot_confirm_resolve_or_learn(
    from_state: CaseState,
    to_state: CaseState,
    kwargs: dict[str, object],
) -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=from_state, version=1)

    with pytest.raises(TransitionAuthorizationError, match="Model identities"):
        apply_transition(snapshot, command(snapshot, to_state, actor=model(), **kwargs))


def test_model_cannot_mutate_even_an_automated_analysis_state() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.INVESTIGATING, version=1)

    with pytest.raises(TransitionAuthorizationError, match="Model identities"):
        apply_transition(snapshot, command(snapshot, CaseState.DIAGNOSED, actor=model()))


def test_learning_requires_indexing_service_and_knowledge_item() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.RESOLVED, version=6)

    with pytest.raises(TransitionAuthorizationError, match="indexing workflow"):
        apply_transition(
            snapshot,
            command(
                snapshot,
                CaseState.LEARNED,
                actor=human(CaseRole.ORG_ADMIN),
                knowledge_item_id=KNOWLEDGE_ITEM_ID,
            ),
        )
    with pytest.raises(TransitionConstraintError, match="knowledge item"):
        apply_transition(snapshot, command(snapshot, CaseState.LEARNED, actor=service()))


def test_needs_information_requires_a_note() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.INVESTIGATING, version=1)

    with pytest.raises(TransitionConstraintError, match="operator-readable note"):
        apply_transition(snapshot, command(snapshot, CaseState.NEEDS_INFORMATION))


def test_auditor_cannot_mutate_cases() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.NEW, version=0)

    with pytest.raises(TransitionAuthorizationError, match="no role"):
        apply_transition(
            snapshot,
            command(snapshot, CaseState.INVESTIGATING, actor=human(CaseRole.AUDITOR)),
        )


def test_commands_snapshots_and_events_are_immutable() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.NEW, version=0)
    input_command = command(snapshot, CaseState.INVESTIGATING)
    outcome = apply_transition(snapshot, input_command)

    with pytest.raises(FrozenInstanceError):
        snapshot.version = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        input_command.to_state = CaseState.DIAGNOSED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        outcome.event.event_type = "tampered"  # type: ignore[misc]


def test_command_rejects_non_utc_or_blank_evidence() -> None:
    snapshot = CaseSnapshot(case_id=CASE_ID, state=CaseState.NEW, version=0)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        TransitionCommand(
            transition_id=TRANSITION_ID,
            event_id=EVENT_ID,
            case_id=CASE_ID,
            expected_version=0,
            to_state=CaseState.INVESTIGATING,
            actor=service(),
            correlation_id=CORRELATION_ID,
            occurred_at=datetime(2026, 7, 15, 13, 0, tzinfo=timezone(timedelta(hours=1))),
        )
    with pytest.raises(ValueError, match="cannot be blank"):
        command(snapshot, CaseState.INVESTIGATING, note="   ")
