"""Unit contracts for artifact completion's transactionally durable fact."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from json import loads
from uuid import UUID

from netops_api.application.artifact_intents import TenantArtifactIntentRepository
from netops_api.application.artifacts import ArtifactObjectMetadata, FakeArtifactStore

ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000002")
INTENT_ID = UUID("00000000-0000-0000-0000-000000000003")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000004")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000005")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000006")
NOW = datetime(2026, 7, 16, tzinfo=UTC)
DIGEST = "a" * 64


class Result:
    def __init__(self, *, mapping: dict[str, object] | None = None, scalar: object = None) -> None:
        self._mapping = mapping
        self._scalar = scalar

    def mappings(self) -> Result:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self._mapping

    def scalar_one(self) -> object:
        return self._scalar


class Savepoint(AbstractContextManager[None]):
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class Connection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._results = iter(
            [
                Result(mapping=intent_row()),
                *[Result() for _ in range(2)],
                Result(scalar=7),
                *[Result() for _ in range(4)],
            ]
        )

    def begin_nested(self) -> Savepoint:
        return Savepoint()

    def execute(self, statement: object, parameters: dict[str, object]) -> Result:
        self.executed.append((str(statement), parameters))
        return next(self._results)


def intent_row() -> dict[str, object]:
    return {
        "id": INTENT_ID,
        "artifact_id": ARTIFACT_ID,
        "case_id": CASE_ID,
        "artifact_kind": "network-configuration",
        "classification": "raw",
        "storage_key": "private-object-key-never-in-event",
        "sha256": DIGEST,
        "byte_size": 42,
        "content_type": "text/plain",
        "original_filename": "edge-router.conf",
        "created_by_actor_id": ACTOR_ID,
        "correlation_id": CORRELATION_ID,
        "expires_at": NOW + timedelta(minutes=5),
        "status": "pending",
    }


def test_verified_completion_writes_metadata_only_outbox_fact_in_same_savepoint() -> None:
    connection = Connection()
    store = FakeArtifactStore()
    store.objects[ARTIFACT_ID] = ArtifactObjectMetadata(
        content_type="text/plain",
        content_length=42,
        sha256_hex=DIGEST,
        encryption_key_reference="kms:local",
    )

    result = TenantArtifactIntentRepository(connection, ORGANIZATION_ID).complete(  # type: ignore[arg-type]
        case_id=CASE_ID, intent_id=INTENT_ID, store=store, now=NOW
    )

    assert result.artifact_id == ARTIFACT_ID
    assert result.already_completed is False
    event_parameters = connection.executed[4][1]
    outbox_parameters = connection.executed[5][1]
    payload = loads(str(outbox_parameters["payload"]))
    assert event_parameters["event_id"] == outbox_parameters["case_event_id"]
    assert outbox_parameters["event_type"] == "artifact.completed.v1"
    assert outbox_parameters["aggregate_version"] == 7
    assert payload == {
        "artifact_id": str(ARTIFACT_ID),
        "artifact_kind": "network-configuration",
        "case_id": str(CASE_ID),
        "classification": "raw",
        "sha256": DIGEST,
    }
    serialized = str(payload)
    assert "private-object-key" not in serialized
    assert "edge-router.conf" not in serialized
    assert "kms:local" not in serialized
