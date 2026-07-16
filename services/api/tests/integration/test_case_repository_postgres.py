"""Real PostgreSQL contracts for the tenant-scoped case repository.

These tests deliberately use the same isolated runtime role and tenant transaction
boundary as production.  Unit tests cover SQL shape; this module proves the deferred
projection-history constraints and PostgreSQL compare-and-swap behavior commit as a
single command.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from netops_api.application.cases import (
    CreateCaseCommand,
    IdempotencyConflictError,
    TenantCaseRepository,
)
from netops_api.core.database import TenantDatabase
from netops_api.domain.cases import (
    Actor,
    ActorKind,
    CaseRole,
    CaseState,
    TransitionCommand,
    apply_transition,
)
from netops_api.domain.errors import VersionConflictError

from .test_tenant_rls import ORGANIZATION_A, _prepared_tenants, _required_url

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 16, tzinfo=UTC)
ACTOR_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677a1")
CORRELATION_ID = UUID("018f0b3c-5e8a-7f0a-8ac4-3344556677a2")


@pytest.fixture(scope="module")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(_required_url("NETOPS_RLS_OWNER_DATABASE_URL"))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def application_engine() -> Iterator[Engine]:
    engine = create_engine(_required_url("NETOPS_RLS_TEST_DATABASE_URL"))
    try:
        yield engine
    finally:
        engine.dispose()


def _actor() -> Actor:
    return Actor(ACTOR_ID, ActorKind.HUMAN, frozenset({CaseRole.OPERATOR}))


def _create_command(*, idempotency_key: str = "repository-create-1") -> CreateCaseCommand:
    return CreateCaseCommand(
        case_id=uuid4(),
        event_id=uuid4(),
        idempotency_key=idempotency_key,
        title="IPsec tunnel unavailable",
        category="ipsec",
        severity="high",
        asset_id=None,
        actor=_actor(),
        correlation_id=CORRELATION_ID,
        occurred_at=NOW,
    )


def _transition_command(case_id: UUID) -> TransitionCommand:
    return TransitionCommand(
        transition_id=uuid4(),
        event_id=uuid4(),
        case_id=case_id,
        expected_version=0,
        to_state=CaseState.INVESTIGATING,
        actor=_actor(),
        correlation_id=uuid4(),
        occurred_at=NOW,
    )


def _create_committed_case(database: TenantDatabase) -> CreateCaseCommand:
    command = _create_command()
    with database.tenant_connection(ORGANIZATION_A) as connection:
        result = TenantCaseRepository(connection, ORGANIZATION_A).create_case(command)
        # The migration's creation-history constraint is deferred. Force it now
        # so this test proves the repository has written its event and outbox
        # rows before the surrounding tenant transaction commits.
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.created is True
    return command


def test_repository_create_then_transition_commits_deferred_history_constraints(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        created = _create_committed_case(database)
        transition = _transition_command(created.case_id)

        with database.tenant_connection(ORGANIZATION_A) as connection:
            repository = TenantCaseRepository(connection, ORGANIZATION_A)
            outcome = apply_transition(repository.get_snapshot(created.case_id), transition)
            updated = repository.persist_transition(outcome, transition.actor)
            # The transition trigger checks its matching transition, event, and
            # outbox records at the transaction boundary, not merely statement
            # time. This would raise if any one of those writes were missing.
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert updated.state is CaseState.INVESTIGATING
        assert updated.version == 1
        with database.tenant_connection(ORGANIZATION_A) as connection:
            detail = TenantCaseRepository(connection, ORGANIZATION_A).get_detail(created.case_id)
            assert detail.case.state is CaseState.INVESTIGATING
            assert detail.case.version == 1
            assert [(entry.event_type, entry.aggregate_version) for entry in detail.timeline] == [
                ("case.created.v1", 0),
                ("case.investigating.v1", 1),
            ]
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM case_transitions WHERE case_id = :case_id"),
                    {"case_id": created.case_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE case_id = :case_id"),
                    {"case_id": created.case_id},
                )
                == 2
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM audit_events WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": ORGANIZATION_A},
                )
                == 2
            )


def test_repository_compare_and_swap_allows_one_concurrent_winner(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        created = _create_committed_case(database)
        commands = (_transition_command(created.case_id), _transition_command(created.case_id))

        def attempt(command: TransitionCommand) -> tuple[Literal["updated", "conflict"], int]:
            try:
                with database.tenant_connection(ORGANIZATION_A) as connection:
                    repository = TenantCaseRepository(connection, ORGANIZATION_A)
                    # Each request reads the same version before either CAS
                    # write begins. PostgreSQL must serialize the writes and
                    # reject exactly one stale projection update.
                    snapshot = repository.get_snapshot(command.case_id)
                    outcomes_ready.append(None)
                    start_writes.wait(timeout=10)
                    outcome = apply_transition(snapshot, command)
                    result = repository.persist_transition(outcome, command.actor)
                    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                return ("updated", result.version)
            except VersionConflictError as error:
                return ("conflict", error.actual_version)

        # A condition-free barrier avoids test scheduling accidentally turning
        # the two requests into sequential reads followed by a unit-test-like
        # stale command. Both transactions are open when their writes start.
        from threading import Barrier

        outcomes_ready: list[None] = []
        start_writes = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt, command) for command in commands]
            results = [future.result(timeout=20) for future in futures]

        assert len(outcomes_ready) == 2
        assert sorted(results) == [("conflict", 1), ("updated", 1)]
        with database.tenant_connection(ORGANIZATION_A) as connection:
            repository = TenantCaseRepository(connection, ORGANIZATION_A)
            assert repository.get_snapshot(created.case_id).version == 1
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM case_transitions WHERE case_id = :case_id"),
                    {"case_id": created.case_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM case_events WHERE case_id = :case_id"),
                    {"case_id": created.case_id},
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE case_id = :case_id"),
                    {"case_id": created.case_id},
                )
                == 2
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM audit_events WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": ORGANIZATION_A},
                )
                == 2
            )


def test_repository_idempotency_replays_only_the_same_canonical_request(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)
    command = _create_command(idempotency_key="same-key-repository-contract")
    replay = replace(
        command,
        case_id=uuid4(),
        event_id=uuid4(),
        correlation_id=uuid4(),
    )
    different_body = replace(
        replay,
        case_id=uuid4(),
        event_id=uuid4(),
        title="Different IPsec tunnel incident",
    )

    with _prepared_tenants(owner_engine):
        with database.tenant_connection(ORGANIZATION_A) as connection:
            created = TenantCaseRepository(connection, ORGANIZATION_A).create_case(command)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        with database.tenant_connection(ORGANIZATION_A) as connection:
            retried = TenantCaseRepository(connection, ORGANIZATION_A).create_case(replay)

        assert created.created is True
        assert retried.created is False
        assert retried.case.case_id == command.case_id

        with database.tenant_connection(ORGANIZATION_A) as connection:
            with pytest.raises(IdempotencyConflictError) as conflict:
                TenantCaseRepository(connection, ORGANIZATION_A).create_case(different_body)
            assert conflict.value.existing_case_id == command.case_id
            assert conflict.value.requested_case_id == different_body.case_id

        with database.tenant_connection(ORGANIZATION_A) as connection:
            assert connection.scalar(text("SELECT count(*) FROM cases")) == 1
            assert connection.scalar(text("SELECT count(*) FROM case_events")) == 1
            assert connection.scalar(text("SELECT count(*) FROM outbox_events")) == 1
            assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 1


def test_repository_rolls_back_projection_when_late_history_insert_fails(
    owner_engine: Engine, application_engine: Engine
) -> None:
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        created = _create_committed_case(database)
        duplicate_event = replace(_transition_command(created.case_id), event_id=created.event_id)

        with database.tenant_connection(ORGANIZATION_A) as connection:
            repository = TenantCaseRepository(connection, ORGANIZATION_A)
            outcome = apply_transition(repository.get_snapshot(created.case_id), duplicate_event)
            # The projection update and transition insert occur before the
            # duplicate case-event key is detected. The repository savepoint
            # must nevertheless erase every partial write from this command.
            with pytest.raises(IntegrityError):
                repository.persist_transition(outcome, duplicate_event.actor)

        with database.tenant_connection(ORGANIZATION_A) as connection:
            repository = TenantCaseRepository(connection, ORGANIZATION_A)
            snapshot = repository.get_snapshot(created.case_id)
            assert snapshot.state is CaseState.NEW
            assert snapshot.version == 0
            assert connection.scalar(text("SELECT count(*) FROM case_transitions")) == 0
            assert connection.scalar(text("SELECT count(*) FROM case_events")) == 1
            assert connection.scalar(text("SELECT count(*) FROM outbox_events")) == 1
            assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 1
