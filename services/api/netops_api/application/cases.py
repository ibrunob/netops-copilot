"""Tenant-scoped case persistence service built on the pure case state machine.

This module deliberately contains no HTTP types. Callers must pass a SQLAlchemy
connection obtained from :class:`TenantDatabase`'s tenant transaction boundary;
the repository includes ``organization_id`` in every statement as a defense in
depth measure while PostgreSQL RLS remains the enforcement point.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, NoReturn, Protocol
from uuid import UUID, uuid4

from sqlalchemy import Connection, Result, text

from netops_api.domain.cases import (
    Actor,
    CaseEventCommand,
    CaseSnapshot,
    CaseState,
    CaseStateTransition,
    TransitionCommand,
    TransitionOutcome,
    apply_transition,
)
from netops_api.domain.errors import VersionConflictError


class CaseNotFoundError(LookupError):
    """Raised when the tenant-scoped case projection does not exist."""

    def __init__(self, case_id: UUID) -> None:
        super().__init__(f"Case {case_id} was not found in the current organization.")
        self.case_id = case_id


class IdempotencyConflictError(ValueError):
    """Raised when one idempotency key is replayed with a different case identity."""

    def __init__(
        self,
        idempotency_key: str,
        existing_case_id: UUID,
        requested_case_id: UUID,
    ) -> None:
        super().__init__("The idempotency key was already used for another case.")
        self.idempotency_key = idempotency_key
        self.existing_case_id = existing_case_id
        self.requested_case_id = requested_case_id


@dataclass(frozen=True, slots=True)
class CreateCaseCommand:
    """Immutable request to create a tenant-owned case and its creation outbox event."""

    case_id: UUID
    event_id: UUID
    idempotency_key: str
    title: str
    category: str | None
    severity: str
    asset_id: UUID | None
    actor: Actor
    correlation_id: UUID
    occurred_at: datetime
    case_input: CaseInputCommand | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip() or len(self.idempotency_key) > 255:
            raise ValueError("idempotency_key must contain 1 to 255 non-blank characters.")
        if not self.title.strip() or len(self.title) > 500:
            raise ValueError("title must contain 1 to 500 non-blank characters.")
        if self.category is not None and (not self.category.strip() or len(self.category) > 100):
            raise ValueError("category must be non-blank and at most 100 characters when supplied.")
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("severity must be low, medium, high, or critical.")
        if self.case_input is not None:
            try:
                json.dumps(self.case_input.content, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise ValueError("case_input content must be JSON serializable.") from exc
        if self.occurred_at.tzinfo is not UTC or self.occurred_at.utcoffset() != UTC.utcoffset(
            self.occurred_at
        ):
            raise ValueError("occurred_at must be timezone-aware UTC.")

    @property
    def request_sha256(self) -> str:
        """Return a canonical request fingerprint used to defend idempotency-key replays."""
        input_command = self.case_input
        payload = {
            "asset_id": str(self.asset_id) if self.asset_id is not None else None,
            "category": self.category,
            "input": (
                {
                    "content": input_command.content,
                    "content_sha256": input_command.content_sha256,
                    "input_kind": input_command.input_kind,
                }
                if input_command is not None
                else None
            ),
            "severity": self.severity,
            "title": self.title,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CaseInputCommand:
    """Optional immutable operator input captured with case creation."""

    input_id: UUID
    input_kind: str
    content_sha256: str
    content: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.input_kind.strip() or len(self.input_kind) > 100:
            raise ValueError("input_kind must contain 1 to 100 non-blank characters.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("content_sha256 must be a lower-case SHA-256 hex digest.")


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """Tenant-visible materialized case projection returned by persistence reads."""

    case_id: UUID
    state: CaseState
    version: int
    title: str
    category: str | None
    severity: str
    asset_id: UUID | None
    created_by_actor_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CaseListCursor:
    """Exclusive stable-order boundary for a page of case projections."""

    updated_at: datetime
    case_id: UUID


@dataclass(frozen=True, slots=True)
class CaseListPage:
    """A bounded case page with an optional cursor for its next page."""

    items: tuple[CaseRecord, ...]
    next_cursor: CaseListCursor | None


@dataclass(frozen=True, slots=True)
class CaseTimelineEntry:
    """Immutable event/transition history item for one case timeline."""

    event_id: UUID
    event_type: str
    aggregate_version: int
    transition_id: UUID | None
    actor_id: UUID
    correlation_id: UUID
    occurred_at: datetime
    from_state: CaseState | None
    to_state: CaseState | None
    approval_id: UUID | None
    verification_note: str | None
    knowledge_item_id: UUID | None
    note: str | None


@dataclass(frozen=True, slots=True)
class CaseDetail:
    """Case projection plus its ordered immutable event timeline."""

    case: CaseRecord
    timeline: tuple[CaseTimelineEntry, ...]


@dataclass(frozen=True, slots=True)
class CreateCaseResult:
    """Creation outcome; retries return the original projection with ``created=False``."""

    case: CaseRecord
    created: bool


class CasePersistence(Protocol):
    """Port used by the application service and unit-test fakes."""

    def get_snapshot(self, case_id: UUID) -> CaseSnapshot: ...

    def persist_transition(self, outcome: TransitionOutcome, actor: Actor) -> CaseRecord: ...


class CaseService:
    """Coordinate pure transition validation with one tenant-aware persistence port."""

    def __init__(self, repository: CasePersistence) -> None:
        self._repository = repository

    def transition(self, command: TransitionCommand) -> CaseRecord:
        """Validate a command without I/O, then persist its projection and immutable records."""
        snapshot = self._repository.get_snapshot(command.case_id)
        outcome = apply_transition(snapshot, command)
        return self._repository.persist_transition(outcome, command.actor)


class TenantCaseRepository:
    """PostgreSQL repository operating only inside a verified tenant transaction.

    The M2 schema migration owns the tables named in these statements. Every command
    writes its projection, immutable history, outbox event, and audit fact through
    one savepoint.
    """

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def create_case(self, command: CreateCaseCommand) -> CreateCaseResult:
        """Create a projection, event, and outbox row atomically, or return an idempotent retry."""
        with self._atomic():
            inserted = self._one_or_none(
                self._connection.execute(
                    _INSERT_CASE,
                    {
                        "organization_id": self._organization_id,
                        "case_id": command.case_id,
                        "title": command.title,
                        "category": command.category,
                        "severity": command.severity,
                        "asset_id": command.asset_id,
                        "actor_id": command.actor.actor_id,
                        "correlation_id": command.correlation_id,
                        "idempotency_key": command.idempotency_key,
                        "request_sha256": command.request_sha256,
                        "occurred_at": command.occurred_at,
                    },
                )
            )
            if inserted is None:
                existing, existing_request_sha256 = self._require_case_by_idempotency(command)
                if existing_request_sha256 != command.request_sha256:
                    raise IdempotencyConflictError(
                        command.idempotency_key, existing.case_id, command.case_id
                    )
                return CreateCaseResult(case=existing, created=False)

            case = _case_record(inserted)
            event = CaseEventCommand(
                event_id=command.event_id,
                event_type="case.created.v1",
                case_id=case.case_id,
                aggregate_version=case.version,
                transition_id=command.event_id,
                actor_id=command.actor.actor_id,
                correlation_id=command.correlation_id,
                occurred_at=command.occurred_at,
            )
            if command.case_input is not None:
                self._insert_case_input(case.case_id, command)
            self._insert_event_and_outbox(
                event,
                transition_id=None,
                request_sha256=command.request_sha256,
            )
            self._insert_audit_event(
                actor=command.actor,
                action="case.created",
                correlation_id=command.correlation_id,
                occurred_at=command.occurred_at,
                details={
                    "case_id": str(case.case_id),
                    "event_id": str(event.event_id),
                    "event_type": event.event_type,
                    "aggregate_version": event.aggregate_version,
                },
            )
            return CreateCaseResult(case=case, created=True)

    def get_snapshot(self, case_id: UUID) -> CaseSnapshot:
        """Read a current projection for pure transition validation within the tenant."""
        row = self._one_or_none(
            self._connection.execute(
                _SELECT_SNAPSHOT,
                {"organization_id": self._organization_id, "case_id": case_id},
            )
        )
        if row is None:
            raise CaseNotFoundError(case_id)
        return CaseSnapshot(
            case_id=_uuid(row["id"]),
            state=CaseState(str(row["state"])),
            version=int(row["version"]),
        )

    def persist_transition(self, outcome: TransitionOutcome, actor: Actor) -> CaseRecord:
        """Compare-and-swap the projection and insert immutable records in one savepoint."""
        with self._atomic():
            updated = self._one_or_none(
                self._connection.execute(
                    _UPDATE_CASE_VERSION,
                    {
                        "organization_id": self._organization_id,
                        "case_id": outcome.snapshot.case_id,
                        "expected_version": outcome.snapshot.version - 1,
                        "state": outcome.snapshot.state.value,
                        "version": outcome.snapshot.version,
                        "updated_at": outcome.transition.occurred_at,
                    },
                )
            )
            if updated is None:
                self._raise_write_conflict(outcome.snapshot)
            self._insert_transition(outcome.transition, actor)
            self._insert_event_and_outbox(
                outcome.event,
                transition_id=outcome.event.transition_id,
            )
            self._insert_audit_event(
                actor=actor,
                action="case.transitioned",
                correlation_id=outcome.event.correlation_id,
                occurred_at=outcome.event.occurred_at,
                details={
                    "case_id": str(outcome.snapshot.case_id),
                    "event_id": str(outcome.event.event_id),
                    "transition_id": str(outcome.transition.transition_id),
                    "event_type": outcome.event.event_type,
                    "aggregate_version": outcome.event.aggregate_version,
                    "from_state": outcome.transition.from_state.value,
                    "to_state": outcome.transition.to_state.value,
                },
            )
            return _case_record(updated)

    def list_cases(
        self,
        *,
        asset_ids: tuple[UUID, ...] = (),
        limit: int = 50,
        cursor: CaseListCursor | None = None,
        query: str | None = None,
        state: CaseState | None = None,
        severity: str | None = None,
    ) -> CaseListPage:
        """List a cursor page limited to the signed asset scope in stable order."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if query is not None and len(query.strip()) > 100:
            raise ValueError("query must contain at most 100 characters.")
        if severity is not None and severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("severity must be low, medium, high, or critical.")
        rows = self._mappings(
            self._connection.execute(
                _LIST_CASES,
                {
                    "organization_id": self._organization_id,
                    "asset_ids": list(asset_ids),
                    "cursor_case_id": cursor.case_id if cursor is not None else None,
                    "cursor_updated_at": cursor.updated_at if cursor is not None else None,
                    "limit": limit + 1,
                    "query": query.strip() if query is not None and query.strip() else None,
                    "severity": severity,
                    "state": state.value if state is not None else None,
                },
            )
        )
        records = tuple(_case_record(row) for row in rows)
        items = records[:limit]
        next_cursor = (
            CaseListCursor(updated_at=items[-1].updated_at, case_id=items[-1].case_id)
            if len(records) > limit and items
            else None
        )
        return CaseListPage(items=items, next_cursor=next_cursor)

    def get_detail(self, case_id: UUID) -> CaseDetail:
        """Return one tenant-visible projection and its immutable, ordered timeline."""
        row = self._one_or_none(
            self._connection.execute(
                _SELECT_CASE,
                {"organization_id": self._organization_id, "case_id": case_id},
            )
        )
        if row is None:
            raise CaseNotFoundError(case_id)
        timeline_rows = self._mappings(
            self._connection.execute(
                _SELECT_TIMELINE,
                {"organization_id": self._organization_id, "case_id": case_id},
            )
        )
        return CaseDetail(
            case=_case_record(row),
            timeline=tuple(_timeline_entry(timeline_row) for timeline_row in timeline_rows),
        )

    def _require_case_by_idempotency(self, command: CreateCaseCommand) -> tuple[CaseRecord, str]:
        row = self._one_or_none(
            self._connection.execute(
                _SELECT_CASE_BY_IDEMPOTENCY,
                {
                    "organization_id": self._organization_id,
                    "idempotency_key": command.idempotency_key,
                },
            )
        )
        if row is None:
            raise RuntimeError("Case insert did not return a row and no idempotent record exists.")
        return _case_record(row), str(row["request_sha256"])

    def _raise_write_conflict(self, snapshot: CaseSnapshot) -> NoReturn:
        current = self.get_snapshot(snapshot.case_id)
        raise VersionConflictError(
            case_id=snapshot.case_id,
            expected_version=snapshot.version - 1,
            actual_version=current.version,
        )

    def _insert_transition(self, transition: CaseStateTransition, actor: Actor) -> None:
        self._connection.execute(
            _INSERT_TRANSITION,
            {
                "organization_id": self._organization_id,
                "transition_id": transition.transition_id,
                "case_id": transition.case_id,
                "from_state": transition.from_state.value,
                "to_state": transition.to_state.value,
                "version": transition.version,
                "actor_id": transition.actor_id,
                "actor_kind": actor.kind.value,
                "correlation_id": transition.correlation_id,
                "occurred_at": transition.occurred_at,
                "approval_id": transition.approval_id,
                "verification_note": transition.verification_note,
                "knowledge_item_id": transition.knowledge_item_id,
                "note": transition.note,
            },
        )

    def _insert_case_input(self, case_id: UUID, command: CreateCaseCommand) -> None:
        """Insert an optional immutable input with the same tenant/actor/correlation lineage."""
        input_command = command.case_input
        if input_command is None:
            return
        self._connection.execute(
            _INSERT_CASE_INPUT,
            {
                "input_id": input_command.input_id,
                "organization_id": self._organization_id,
                "case_id": case_id,
                "input_kind": input_command.input_kind,
                "content_sha256": input_command.content_sha256,
                "content": json.dumps(input_command.content, separators=(",", ":")),
                "actor_id": command.actor.actor_id,
                "correlation_id": command.correlation_id,
                "occurred_at": command.occurred_at,
            },
        )

    def _insert_event_and_outbox(
        self,
        event: CaseEventCommand,
        *,
        transition_id: UUID | None,
        request_sha256: str | None = None,
    ) -> None:
        payload = json.dumps(
            {
                "case_id": str(event.case_id),
                "aggregate_version": event.aggregate_version,
                "transition_id": str(transition_id) if transition_id is not None else None,
                "actor_id": str(event.actor_id),
                "correlation_id": str(event.correlation_id),
                **({"request_sha256": request_sha256} if request_sha256 is not None else {}),
            },
            separators=(",", ":"),
        )
        parameters = {
            "organization_id": self._organization_id,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "case_id": event.case_id,
            "aggregate_version": event.aggregate_version,
            "transition_id": transition_id,
            "actor_id": event.actor_id,
            "correlation_id": event.correlation_id,
            "occurred_at": event.occurred_at,
            "payload": payload,
        }
        self._connection.execute(_INSERT_EVENT, parameters)
        self._connection.execute(
            _INSERT_OUTBOX,
            {
                **parameters,
                "outbox_id": uuid4(),
                "case_event_id": event.event_id,
                "available_at": event.occurred_at,
                "created_at": event.occurred_at,
            },
        )

    def _insert_audit_event(
        self,
        *,
        actor: Actor,
        action: str,
        correlation_id: UUID,
        occurred_at: datetime,
        details: Mapping[str, object],
    ) -> None:
        """Record a tenant-visible, immutable audit fact in the command savepoint.

        Audit details intentionally contain identifiers and workflow facts only.  In
        particular, user input, case titles, free-form notes, authentication claims,
        and idempotency fingerprints must never be copied into this append-only log.
        """
        self._connection.execute(
            _INSERT_AUDIT_EVENT,
            {
                "audit_event_id": uuid4(),
                "organization_id": self._organization_id,
                "actor_subject": _audit_subject(actor),
                "action": action,
                "correlation_id": correlation_id,
                "details": json.dumps(details, sort_keys=True, separators=(",", ":")),
                "occurred_at": occurred_at,
            },
        )

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """Use a savepoint so a failed immutable write cannot leak a partial command."""
        with self._connection.begin_nested():
            yield

    @staticmethod
    def _mappings(result: Result[Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in result.mappings().all()]

    @classmethod
    def _one_or_none(cls, result: Result[Any]) -> dict[str, Any] | None:
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)


def _case_record(row: dict[str, Any]) -> CaseRecord:
    return CaseRecord(
        case_id=_uuid(row["id"]),
        state=CaseState(str(row["state"])),
        version=int(row["version"]),
        title=str(row["title"]),
        category=_optional_text(row["category"]),
        severity=str(row["severity"]),
        asset_id=_optional_uuid(row["asset_id"]),
        created_by_actor_id=_optional_uuid(row["created_by_actor_id"]),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _timeline_entry(row: dict[str, Any]) -> CaseTimelineEntry:
    return CaseTimelineEntry(
        event_id=_uuid(row["event_id"]),
        event_type=str(row["event_type"]),
        aggregate_version=int(row["aggregate_version"]),
        transition_id=_optional_uuid(row["transition_id"]),
        actor_id=_uuid(row["actor_id"]),
        correlation_id=_uuid(row["correlation_id"]),
        occurred_at=_datetime(row["occurred_at"]),
        from_state=_optional_state(row["from_state"]),
        to_state=_optional_state(row["to_state"]),
        approval_id=_optional_uuid(row["approval_id"]),
        verification_note=_optional_text(row["verification_note"]),
        knowledge_item_id=_optional_uuid(row["knowledge_item_id"]),
        note=_optional_text(row["note"]),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else _uuid(value)


def _optional_state(value: object) -> CaseState | None:
    return None if value is None else CaseState(str(value))


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("Database datetime columns must be returned as datetime values.")
    return value


def _audit_subject(actor: Actor) -> str:
    """Return a stable pseudonymous subject without copying authentication claims."""
    return f"{actor.kind.value}:{actor.actor_id}"


_INSERT_CASE = text(
    """
    INSERT INTO cases (
      id, organization_id, asset_id, title, category, severity, state, version,
      idempotency_key, request_sha256, created_by_actor_id, correlation_id, created_at, updated_at
    ) VALUES (
      :case_id, :organization_id, :asset_id, :title, :category, :severity, 'new', 0,
      :idempotency_key, :request_sha256, :actor_id, :correlation_id, :occurred_at, :occurred_at
    )
    ON CONFLICT (organization_id, idempotency_key) DO NOTHING
    RETURNING id, state, version, title, category, severity, asset_id, created_by_actor_id,
              created_at, updated_at
    """
)
_SELECT_CASE_BY_IDEMPOTENCY = text(
    """
    SELECT c.id, c.state, c.version, c.title, c.category, c.severity, c.asset_id,
           c.created_by_actor_id, c.created_at, c.updated_at, c.request_sha256
    FROM cases AS c
    WHERE c.organization_id = :organization_id
      AND c.idempotency_key = :idempotency_key
    """
)
_SELECT_SNAPSHOT = text(
    """
    SELECT id, state, version
    FROM cases
    WHERE organization_id = :organization_id AND id = :case_id
    """
)
_UPDATE_CASE_VERSION = text(
    """
    UPDATE cases
    SET state = :state, version = :version, updated_at = :updated_at
    WHERE organization_id = :organization_id AND id = :case_id AND version = :expected_version
    RETURNING id, state, version, title, category, severity, asset_id, created_by_actor_id,
              created_at, updated_at
    """
)
_INSERT_CASE_INPUT = text(
    """
    INSERT INTO case_inputs (
      id, organization_id, case_id, input_kind, content_sha256, content,
      created_by_actor_id, correlation_id, created_at
    ) VALUES (
      :input_id, :organization_id, :case_id, :input_kind, :content_sha256, CAST(:content AS jsonb),
      :actor_id, :correlation_id, :occurred_at
    )
    """
)
_INSERT_TRANSITION = text(
    """
    INSERT INTO case_transitions (
      id, organization_id, case_id, from_state, to_state, version, actor_id,
      actor_kind, correlation_id, occurred_at, approval_id, verification_note,
      knowledge_item_id, note
    ) VALUES (
      :transition_id, :organization_id, :case_id, :from_state, :to_state, :version, :actor_id,
      :actor_kind, :correlation_id, :occurred_at, :approval_id, :verification_note,
      :knowledge_item_id, :note
    )
    """
)
_INSERT_EVENT = text(
    """
    INSERT INTO case_events (
      id, organization_id, case_id, event_type, aggregate_version, transition_id,
      actor_id, correlation_id, occurred_at, payload
    ) VALUES (
      :event_id, :organization_id, :case_id, :event_type, :aggregate_version, :transition_id,
      :actor_id, :correlation_id, :occurred_at, CAST(:payload AS jsonb)
    )
    """
)
_INSERT_OUTBOX = text(
    """
    INSERT INTO outbox_events (
      id, organization_id, case_id, case_event_id, event_type, aggregate_version,
      correlation_id, payload, available_at, created_at
    ) VALUES (
      :outbox_id, :organization_id, :case_id, :case_event_id, :event_type, :aggregate_version,
      :correlation_id, CAST(:payload AS jsonb), :available_at, :created_at
    )
    """
)
_INSERT_AUDIT_EVENT = text(
    """
    INSERT INTO audit_events (
      id, organization_id, actor_subject, action, correlation_id, details, occurred_at
    ) VALUES (
      :audit_event_id, :organization_id, :actor_subject, :action, :correlation_id,
      CAST(:details AS json), :occurred_at
    )
    """
)
_LIST_CASES = text(
    """
    SELECT id, state, version, title, category, severity, asset_id, created_by_actor_id,
           created_at, updated_at
    FROM cases
    WHERE organization_id = :organization_id
      AND (asset_id IS NULL OR asset_id = ANY(CAST(:asset_ids AS uuid[])))
      AND (CAST(:state AS text) IS NULL OR state = CAST(:state AS text))
      AND (CAST(:severity AS text) IS NULL OR severity = CAST(:severity AS text))
      AND (
        CAST(:query AS text) IS NULL
        OR title ILIKE '%' || CAST(:query AS text) || '%'
        OR COALESCE(category, '') ILIKE '%' || CAST(:query AS text) || '%'
        OR CAST(id AS text) ILIKE '%' || CAST(:query AS text) || '%'
      )
      AND (
        CAST(:cursor_updated_at AS timestamptz) IS NULL
        OR (updated_at, id) < (
          CAST(:cursor_updated_at AS timestamptz), CAST(:cursor_case_id AS uuid)
        )
      )
    ORDER BY updated_at DESC, id DESC
    LIMIT :limit
    """
)
_SELECT_CASE = text(
    """
    SELECT id, state, version, title, category, severity, asset_id, created_by_actor_id,
           created_at, updated_at
    FROM cases
    WHERE organization_id = :organization_id AND id = :case_id
    """
)
_SELECT_TIMELINE = text(
    """
    SELECT e.id AS event_id, e.event_type, e.aggregate_version, e.transition_id,
           e.actor_id, e.correlation_id, e.occurred_at, t.from_state, t.to_state,
           t.approval_id, t.verification_note, t.knowledge_item_id, t.note
    FROM case_events AS e
    LEFT JOIN case_transitions AS t
      ON t.organization_id = e.organization_id AND t.id = e.transition_id
    WHERE e.organization_id = :organization_id AND e.case_id = :case_id
    ORDER BY e.aggregate_version ASC, e.occurred_at ASC, e.id ASC
    """
)
