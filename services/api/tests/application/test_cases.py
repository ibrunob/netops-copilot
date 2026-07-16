from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from json import loads
from typing import Any
from uuid import UUID, uuid4

import pytest

from netops_api.application.cases import (
    CaseListCursor,
    CaseRecord,
    CaseService,
    CreateCaseCommand,
    IdempotencyConflictError,
    TenantCaseRepository,
)
from netops_api.domain.cases import (
    Actor,
    ActorKind,
    CaseRole,
    CaseSnapshot,
    CaseState,
    TransitionCommand,
    TransitionOutcome,
    apply_transition,
)
from netops_api.domain.errors import InvalidTransitionError

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000002")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000003")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 7, 15, tzinfo=UTC)


class FakeMappings:
    def __init__(
        self, one: dict[str, Any] | None = None, all_rows: list[dict[str, Any]] | None = None
    ):
        self._one = one
        self._all_rows = all_rows if all_rows is not None else ([] if one is None else [one])

    def one_or_none(self) -> dict[str, Any] | None:
        return self._one

    def all(self) -> list[dict[str, Any]]:
        return self._all_rows


class FakeResult:
    def __init__(
        self, one: dict[str, Any] | None = None, all_rows: list[dict[str, Any]] | None = None
    ):
        self._mappings = FakeMappings(one, all_rows)

    def mappings(self) -> FakeMappings:
        return self._mappings


class FakeSavepoint(AbstractContextManager[None]):
    def __init__(self, connection: FakeConnection):
        self._connection = connection

    def __enter__(self) -> None:
        self._connection.savepoints += 1
        return None

    def __exit__(self, *_: object) -> None:
        return None


class FakeConnection:
    def __init__(self, results: list[FakeResult]):
        self._results = iter(results)
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.savepoints = 0

    def begin_nested(self) -> FakeSavepoint:
        return FakeSavepoint(self)

    def execute(self, statement: object, parameters: dict[str, Any]) -> FakeResult:
        self.executed.append((str(statement), parameters))
        return next(self._results)


def case_row(*, version: int = 0) -> dict[str, Any]:
    return {
        "id": CASE_ID,
        "state": "new" if version == 0 else "investigating",
        "version": version,
        "title": "VPN tunnel down",
        "category": "ipsec",
        "severity": "high",
        "asset_id": None,
        "created_by_actor_id": ACTOR_ID,
        "created_at": NOW,
        "updated_at": NOW,
    }


def actor() -> Actor:
    return Actor(actor_id=ACTOR_ID, kind=ActorKind.HUMAN, roles=frozenset({CaseRole.OPERATOR}))


def create_command() -> CreateCaseCommand:
    return CreateCaseCommand(
        case_id=CASE_ID,
        event_id=uuid4(),
        idempotency_key="case-create-1",
        title="VPN tunnel down",
        category="ipsec",
        severity="high",
        asset_id=None,
        actor=actor(),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
    )


def test_create_persists_projection_and_immutable_event_outbox_records() -> None:
    connection = FakeConnection([FakeResult(case_row()), FakeResult(), FakeResult(), FakeResult()])
    repository = TenantCaseRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]
    command = create_command()

    result = repository.create_case(command)

    assert result.created is True
    assert result.case.title == "VPN tunnel down"
    assert connection.savepoints == 1
    assert len(connection.executed) == 4
    event_parameters = connection.executed[1][1]
    outbox_parameters = connection.executed[2][1]
    audit_parameters = connection.executed[3][1]
    assert event_parameters["transition_id"] is None
    assert event_parameters["correlation_id"] == CORRELATION_ID
    assert '"request_sha256"' in event_parameters["payload"]
    assert outbox_parameters["case_event_id"] == event_parameters["event_id"]
    assert outbox_parameters["case_id"] == result.case.case_id
    assert outbox_parameters["outbox_id"] != event_parameters["event_id"]
    assert audit_parameters["actor_subject"] == f"human:{ACTOR_ID}"
    assert audit_parameters["action"] == "case.created"
    assert audit_parameters["correlation_id"] == CORRELATION_ID
    assert loads(audit_parameters["details"]) == {
        "aggregate_version": 0,
        "case_id": str(CASE_ID),
        "event_id": str(event_parameters["event_id"]),
        "event_type": "case.created.v1",
    }
    assert "VPN tunnel down" not in audit_parameters["details"]
    assert command.idempotency_key not in audit_parameters["details"]


def test_create_idempotency_replay_returns_original_case_only_when_request_matches() -> None:
    command = create_command()
    replay_row = {**case_row(), "request_sha256": command.request_sha256}
    connection = FakeConnection([FakeResult(), FakeResult(replay_row)])
    repository = TenantCaseRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    result = repository.create_case(command)

    assert result.created is False
    assert result.case == CaseRecord(
        case_id=CASE_ID,
        state=CaseState.NEW,
        version=0,
        title="VPN tunnel down",
        category="ipsec",
        severity="high",
        asset_id=None,
        created_by_actor_id=ACTOR_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    assert len(connection.executed) == 2
    assert all("audit_events" not in statement for statement, _ in connection.executed)


def test_create_rejects_idempotency_key_reused_for_different_canonical_request() -> None:
    command = create_command()
    connection = FakeConnection(
        [FakeResult(), FakeResult({**case_row(), "request_sha256": "0" * 64})]
    )
    repository = TenantCaseRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    with pytest.raises(IdempotencyConflictError):
        repository.create_case(command)

    assert all("audit_events" not in statement for statement, _ in connection.executed)


@dataclass
class RecordingPersistence:
    snapshot: CaseSnapshot
    outcome: TransitionOutcome | None = None
    persisted_actor: Actor | None = None

    def get_snapshot(self, _: UUID) -> CaseSnapshot:
        return self.snapshot

    def persist_transition(self, outcome: TransitionOutcome, actor_value: Actor) -> CaseRecord:
        self.outcome = outcome
        self.persisted_actor = actor_value
        return CaseRecord(
            case_id=outcome.snapshot.case_id,
            state=outcome.snapshot.state,
            version=outcome.snapshot.version,
            title="VPN tunnel down",
            category=None,
            severity="medium",
            asset_id=None,
            created_by_actor_id=ACTOR_ID,
            created_at=NOW,
            updated_at=NOW,
        )


def test_service_only_persists_a_transition_accepted_by_the_pure_state_machine() -> None:
    persistence = RecordingPersistence(CaseSnapshot(CASE_ID, CaseState.NEW, 0))
    service = CaseService(persistence)
    command = TransitionCommand(
        transition_id=uuid4(),
        event_id=uuid4(),
        case_id=CASE_ID,
        expected_version=0,
        to_state=CaseState.INVESTIGATING,
        actor=actor(),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
    )

    result = service.transition(command)

    assert result.state is CaseState.INVESTIGATING
    assert persistence.outcome is not None
    assert persistence.persisted_actor == actor()


def test_service_does_not_write_an_invalid_direct_state_change() -> None:
    persistence = RecordingPersistence(CaseSnapshot(CASE_ID, CaseState.NEW, 0))
    service = CaseService(persistence)
    command = TransitionCommand(
        transition_id=uuid4(),
        event_id=uuid4(),
        case_id=CASE_ID,
        expected_version=0,
        to_state=CaseState.RESOLVED,
        actor=actor(),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
        verification_note="validated by operator",
    )

    with pytest.raises(InvalidTransitionError):
        service.transition(command)

    assert persistence.outcome is None


def test_repository_uses_compare_and_swap_then_captures_transition_actor_and_outbox() -> None:
    command = TransitionCommand(
        transition_id=uuid4(),
        event_id=uuid4(),
        case_id=CASE_ID,
        expected_version=0,
        to_state=CaseState.INVESTIGATING,
        actor=actor(),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
    )
    outcome = apply_transition(CaseSnapshot(CASE_ID, CaseState.NEW, 0), command)
    connection = FakeConnection(
        [FakeResult(case_row(version=1)), FakeResult(), FakeResult(), FakeResult(), FakeResult()]
    )
    repository = TenantCaseRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    result = repository.persist_transition(outcome, command.actor)

    assert result.version == 1
    assert connection.savepoints == 1
    update_parameters = connection.executed[0][1]
    transition_parameters = connection.executed[1][1]
    event_parameters = connection.executed[2][1]
    outbox_parameters = connection.executed[3][1]
    audit_parameters = connection.executed[4][1]
    assert update_parameters["expected_version"] == 0
    assert transition_parameters["actor_id"] == ACTOR_ID
    assert transition_parameters["actor_kind"] == "human"
    assert transition_parameters["correlation_id"] == CORRELATION_ID
    assert event_parameters["transition_id"] == command.transition_id
    assert outbox_parameters["case_event_id"] == command.event_id
    assert audit_parameters["actor_subject"] == f"human:{ACTOR_ID}"
    assert audit_parameters["action"] == "case.transitioned"
    assert audit_parameters["correlation_id"] == CORRELATION_ID
    assert loads(audit_parameters["details"]) == {
        "aggregate_version": 1,
        "case_id": str(CASE_ID),
        "event_id": str(command.event_id),
        "event_type": "case.investigating.v1",
        "from_state": "new",
        "to_state": "investigating",
        "transition_id": str(command.transition_id),
    }


def test_repository_reads_tenant_scoped_list_and_event_timeline() -> None:
    timeline_row = {
        "event_id": uuid4(),
        "event_type": "case.investigating.v1",
        "aggregate_version": 1,
        "transition_id": uuid4(),
        "actor_id": ACTOR_ID,
        "correlation_id": CORRELATION_ID,
        "occurred_at": NOW,
        "from_state": "new",
        "to_state": "investigating",
        "approval_id": None,
        "verification_note": None,
        "knowledge_item_id": None,
        "note": None,
    }
    connection = FakeConnection(
        [
            FakeResult(all_rows=[case_row(version=1)]),
            FakeResult(case_row(version=1)),
            FakeResult(all_rows=[timeline_row]),
        ]
    )
    repository = TenantCaseRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    listed = repository.list_cases(limit=10)
    detail = repository.get_detail(CASE_ID)

    assert listed.items[0].state is CaseState.INVESTIGATING
    assert detail.case.case_id == CASE_ID
    assert detail.timeline[0].to_state is CaseState.INVESTIGATING
    assert all(
        parameters["organization_id"] == ORGANIZATION_ID for _, parameters in connection.executed
    )
    list_statement, list_parameters = connection.executed[0]
    assert "(updated_at, id) <" in list_statement
    assert "title ILIKE" in list_statement
    assert list_parameters["limit"] == 11
    assert list_parameters["cursor_updated_at"] is None


def test_repository_uses_exclusive_cursor_and_fetches_one_extra_row() -> None:
    second_case_id = UUID("00000000-0000-0000-0000-000000000005")
    second_updated_at = datetime(2026, 7, 14, tzinfo=UTC)
    second_row = case_row()
    second_row["id"] = second_case_id
    second_row["updated_at"] = second_updated_at
    cursor = CaseListCursor(updated_at=NOW, case_id=CASE_ID)
    connection = FakeConnection([FakeResult(all_rows=[case_row(), second_row])])
    repository = TenantCaseRepository(connection, ORGANIZATION_ID)  # type: ignore[arg-type]

    page = repository.list_cases(
        limit=1,
        cursor=cursor,
        query="vpn",
        state=CaseState.INVESTIGATING,
        severity="high",
    )

    assert page.items == (page.items[0],)
    assert page.next_cursor == CaseListCursor(updated_at=NOW, case_id=CASE_ID)
    _, parameters = connection.executed[0]
    assert parameters["limit"] == 2
    assert parameters["cursor_updated_at"] == NOW
    assert parameters["cursor_case_id"] == CASE_ID
    assert parameters["query"] == "vpn"
    assert parameters["state"] == "investigating"
    assert parameters["severity"] == "high"
