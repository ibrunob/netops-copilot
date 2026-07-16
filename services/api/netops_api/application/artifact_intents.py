"""Tenant-scoped persistence for byte-free artifact upload intents."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from netops_api.application.artifacts import (
    ArtifactObjectMetadata,
    ArtifactStore,
    ArtifactUploadRequest,
)


@dataclass(frozen=True, slots=True)
class CreateArtifactUploadIntent:
    """Declared immutable metadata for a future object-store upload.

    This is intentionally separate from ``artifacts``: an intent is not evidence
    until a later completion step verifies the uploaded object's metadata.
    """

    intent_id: UUID
    artifact_id: UUID
    case_id: UUID
    artifact_kind: str
    classification: str
    content_type: str
    byte_size: int
    sha256_hex: str
    original_filename: str | None
    actor_id: UUID
    correlation_id: UUID
    created_at: datetime
    expires_at: datetime


class ArtifactUploadIntentNotFoundError(LookupError):
    """An upload intent is not visible in the current tenant/case scope."""


class ArtifactUploadIntentExpiredError(ValueError):
    """The client attempted to finalize after the capability deadline."""


class ArtifactUploadVerificationError(ValueError):
    """Store metadata did not exactly match the signed upload declaration."""


@dataclass(frozen=True, slots=True)
class CompletedArtifactUpload:
    """Safe completion result; object key and bytes stay internal."""

    artifact_id: UUID
    completed_at: datetime
    already_completed: bool


@dataclass(frozen=True, slots=True)
class _UploadIntent:
    intent_id: UUID
    artifact_id: UUID
    case_id: UUID
    artifact_kind: str
    classification: str
    storage_key: str
    sha256_hex: str
    byte_size: int
    content_type: str
    original_filename: str | None
    created_by_actor_id: UUID | None
    correlation_id: UUID
    expires_at: datetime
    status: str


class TenantArtifactIntentRepository:
    """Write upload declarations and safe audit facts inside the tenant transaction."""

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def create(self, command: CreateArtifactUploadIntent) -> None:
        with self._atomic():
            self._connection.execute(
                _INSERT_UPLOAD_INTENT,
                {
                    "intent_id": command.intent_id,
                    "artifact_id": command.artifact_id,
                    "organization_id": self._organization_id,
                    "case_id": command.case_id,
                    "artifact_kind": command.artifact_kind,
                    "classification": command.classification,
                    "storage_key": self._storage_key(command),
                    "sha256": command.sha256_hex,
                    "byte_size": command.byte_size,
                    "content_type": command.content_type,
                    "original_filename": command.original_filename,
                    "created_by_actor_id": command.actor_id,
                    "correlation_id": command.correlation_id,
                    "expires_at": command.expires_at,
                    "created_at": command.created_at,
                },
            )
            self._connection.execute(
                _INSERT_AUDIT_EVENT,
                {
                    "audit_event_id": uuid4(),
                    "organization_id": self._organization_id,
                    "actor_subject": f"human:{command.actor_id}",
                    "action": "artifact.upload_intent_created",
                    "correlation_id": command.correlation_id,
                    "details": json.dumps(
                        {
                            "artifact_id": str(command.artifact_id),
                            "case_id": str(command.case_id),
                            "intent_id": str(command.intent_id),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "occurred_at": command.created_at,
                },
            )

    def complete(
        self,
        *,
        case_id: UUID,
        intent_id: UUID,
        store: ArtifactStore,
        now: datetime,
    ) -> CompletedArtifactUpload:
        """Verify a private object HEAD and atomically turn an intent into evidence."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        with self._atomic():
            intent = self._load_for_completion(case_id=case_id, intent_id=intent_id)
            if intent.status == "completed":
                return CompletedArtifactUpload(
                    artifact_id=intent.artifact_id,
                    completed_at=self._completed_at(intent_id),
                    already_completed=True,
                )
            if intent.status != "pending":
                raise ArtifactUploadVerificationError("Upload intent is not completable.")
            if intent.expires_at <= now:
                raise ArtifactUploadIntentExpiredError("Upload intent has expired.")
            request = ArtifactUploadRequest(
                organization_id=self._organization_id,
                artifact_id=intent.artifact_id,
                case_id=intent.case_id,
                content_type=intent.content_type,
                content_length=intent.byte_size,
                sha256_hex=intent.sha256_hex,
            )
            object_metadata = store.head_uploaded_object(request)
            self._verify_metadata(intent, object_metadata)
            completed_at = now.astimezone(UTC)
            self._connection.execute(
                _INSERT_ARTIFACT,
                {
                    "artifact_id": intent.artifact_id,
                    "organization_id": self._organization_id,
                    "case_id": intent.case_id,
                    "artifact_kind": intent.artifact_kind,
                    "classification": intent.classification,
                    "storage_key": intent.storage_key,
                    "sha256": intent.sha256_hex,
                    "byte_size": intent.byte_size,
                    "content_type": intent.content_type,
                    "original_filename": intent.original_filename,
                    "encryption_key_reference": object_metadata.encryption_key_reference,
                    "created_by_actor_id": intent.created_by_actor_id,
                    "retention_until": self._retention_until(intent, completed_at),
                    "created_at": completed_at,
                },
            )
            self._connection.execute(
                _INSERT_QUARANTINED_PROCESSING_EVENT,
                {
                    "organization_id": self._organization_id,
                    "artifact_id": intent.artifact_id,
                    "correlation_id": intent.correlation_id,
                    "created_by_actor_id": intent.created_by_actor_id,
                    "occurred_at": completed_at,
                },
            )
            self._insert_completion_outbox(intent, completed_at=completed_at)
            self._connection.execute(
                _MARK_INTENT_COMPLETED,
                {"intent_id": intent.intent_id, "completed_at": completed_at},
            )
            self._connection.execute(
                _INSERT_AUDIT_EVENT,
                {
                    "audit_event_id": uuid4(),
                    "organization_id": self._organization_id,
                    "actor_subject": f"human:{intent.created_by_actor_id}",
                    "action": "artifact.upload_completed",
                    "correlation_id": intent.correlation_id,
                    "details": json.dumps(
                        {
                            "artifact_id": str(intent.artifact_id),
                            "case_id": str(intent.case_id),
                            "intent_id": str(intent.intent_id),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "occurred_at": completed_at,
                },
            )
            return CompletedArtifactUpload(
                artifact_id=intent.artifact_id,
                completed_at=completed_at,
                already_completed=False,
            )

    def _load_for_completion(self, *, case_id: UUID, intent_id: UUID) -> _UploadIntent:
        row = (
            self._connection.execute(
                _SELECT_INTENT_FOR_COMPLETION,
                {
                    "organization_id": self._organization_id,
                    "case_id": case_id,
                    "intent_id": intent_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ArtifactUploadIntentNotFoundError(intent_id)
        return _UploadIntent(
            intent_id=row["id"],
            artifact_id=row["artifact_id"],
            case_id=row["case_id"],
            artifact_kind=row["artifact_kind"],
            classification=row["classification"],
            storage_key=row["storage_key"],
            sha256_hex=row["sha256"],
            byte_size=row["byte_size"],
            content_type=row["content_type"],
            original_filename=row["original_filename"],
            created_by_actor_id=row["created_by_actor_id"],
            correlation_id=row["correlation_id"],
            expires_at=row["expires_at"],
            status=row["status"],
        )

    def _completed_at(self, intent_id: UUID) -> datetime:
        value = self._connection.execute(
            _SELECT_INTENT_COMPLETED_AT, {"intent_id": intent_id}
        ).scalar_one()
        assert isinstance(value, datetime)
        return value

    @staticmethod
    def _verify_metadata(intent: _UploadIntent, observed: ArtifactObjectMetadata) -> None:
        if (
            observed.content_type != intent.content_type
            or observed.content_length != intent.byte_size
            or observed.sha256_hex != intent.sha256_hex
        ):
            raise ArtifactUploadVerificationError(
                "Uploaded object metadata does not match its signed declaration."
            )

    @staticmethod
    def _retention_until(intent: _UploadIntent, completed_at: datetime) -> datetime:
        return completed_at + timedelta(days=30 if intent.artifact_kind == "incident-audio" else 90)

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        with self._connection.begin_nested():
            yield

    def _storage_key(self, command: CreateArtifactUploadIntent) -> str:
        """Keep the deterministic object location inside persistence/storage adapters."""
        return (
            f"organizations/{self._organization_id}/cases/{command.case_id}/"
            f"artifacts/{command.artifact_id}"
        )

    def _insert_completion_outbox(self, intent: _UploadIntent, *, completed_at: datetime) -> None:
        """Append the verified fact and its delivery record in this savepoint.

        Completion is not a case-state transition, so it retains the current
        aggregate version.  Its event type is distinct from transition events;
        the payload is intentionally a closed metadata-only contract.
        """
        aggregate_version = self._connection.execute(
            _SELECT_CASE_VERSION,
            {"organization_id": self._organization_id, "case_id": intent.case_id},
        ).scalar_one()
        event_id = uuid4()
        payload = json.dumps(
            {
                "artifact_id": str(intent.artifact_id),
                "artifact_kind": intent.artifact_kind,
                "case_id": str(intent.case_id),
                "classification": intent.classification,
                "sha256": intent.sha256_hex,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        parameters = {
            "event_id": event_id,
            "organization_id": self._organization_id,
            "case_id": intent.case_id,
            "event_type": "artifact.completed.v1",
            "aggregate_version": aggregate_version,
            "correlation_id": intent.correlation_id,
            "occurred_at": completed_at,
            "payload": payload,
        }
        self._connection.execute(_INSERT_ARTIFACT_COMPLETION_EVENT, parameters)
        self._connection.execute(
            _INSERT_ARTIFACT_COMPLETION_OUTBOX,
            {
                **parameters,
                "outbox_id": uuid4(),
                "case_event_id": event_id,
                "available_at": completed_at,
                "created_at": completed_at,
            },
        )


_INSERT_UPLOAD_INTENT = text(
    """
    INSERT INTO artifact_upload_intents (
      id, artifact_id, organization_id, case_id, artifact_kind, classification,
      storage_key, sha256, byte_size, content_type, original_filename,
      created_by_actor_id, correlation_id, expires_at, created_at
    ) VALUES (
      :intent_id, :artifact_id, :organization_id, :case_id, :artifact_kind, :classification,
      :storage_key, :sha256, :byte_size, :content_type, :original_filename,
      :created_by_actor_id, :correlation_id, :expires_at, :created_at
    )
    """
)

_SELECT_INTENT_FOR_COMPLETION = text(
    """
    SELECT id, artifact_id, case_id, artifact_kind, classification, storage_key,
           sha256, byte_size, content_type, original_filename, created_by_actor_id,
           correlation_id, expires_at, status
      FROM artifact_upload_intents
     WHERE organization_id = :organization_id
       AND case_id = :case_id
       AND id = :intent_id
     FOR UPDATE
    """
)

_SELECT_INTENT_COMPLETED_AT = text(
    "SELECT completed_at FROM artifact_upload_intents WHERE id = :intent_id"
)

_INSERT_ARTIFACT = text(
    """
    INSERT INTO artifacts (
      id, organization_id, case_id, artifact_kind, classification, storage_key,
      sha256, byte_size, content_type, original_filename, encryption_key_reference,
      created_by_actor_id, retention_until, created_at
    ) VALUES (
      :artifact_id, :organization_id, :case_id, :artifact_kind, :classification, :storage_key,
      :sha256, :byte_size, :content_type, :original_filename, :encryption_key_reference,
      :created_by_actor_id, :retention_until, :created_at
    )
    """
)

_MARK_INTENT_COMPLETED = text(
    """
    UPDATE artifact_upload_intents
       SET status = 'completed', completed_at = :completed_at
     WHERE id = :intent_id AND status = 'pending'
    """
)

_INSERT_QUARANTINED_PROCESSING_EVENT = text(
    """
    INSERT INTO artifact_processing_events (
      organization_id, artifact_id, attempt, state, processor, processor_version,
      result_summary, correlation_id, created_by_actor_id, occurred_at
    ) VALUES (
      :organization_id, :artifact_id, 1, 'quarantined', 'artifact-upload-verifier', 'v1',
      '{"metadata_verified":true}'::jsonb, :correlation_id, :created_by_actor_id, :occurred_at
    )
    """
)

_SELECT_CASE_VERSION = text(
    "SELECT version FROM cases WHERE organization_id = :organization_id AND id = :case_id"
)

_INSERT_ARTIFACT_COMPLETION_EVENT = text(
    """
    INSERT INTO case_events (
      id, organization_id, case_id, event_type, aggregate_version, transition_id,
      actor_id, correlation_id, occurred_at, payload
    ) VALUES (
      :event_id, :organization_id, :case_id, :event_type, :aggregate_version, NULL,
      NULL, :correlation_id, :occurred_at, CAST(:payload AS jsonb)
    )
    """
)

_INSERT_ARTIFACT_COMPLETION_OUTBOX = text(
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
