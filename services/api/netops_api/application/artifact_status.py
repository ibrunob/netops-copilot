"""Tenant-scoped, metadata-free read model for artifact intake status.

This projection deliberately contains only lifecycle state.  It never selects
object locations, hashes, filenames, media types, byte counts, or artifact
contents.  The projection includes the latest immutable processing-ledger fact
when one exists; it does not itself trigger processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, text


@dataclass(frozen=True, slots=True)
class ArtifactStatus:
    """A safe case-visible lifecycle projection for one artifact."""

    artifact_id: UUID
    artifact_kind: str
    status: str
    status_updated_at: datetime


class TenantArtifactStatusRepository:
    """Read artifact lifecycle state under the caller's RLS tenant transaction."""

    def __init__(self, connection: Connection, organization_id: UUID) -> None:
        self._connection = connection
        self._organization_id = organization_id

    def list_for_case(self, case_id: UUID) -> tuple[ArtifactStatus, ...]:
        """Return lifecycle states without loading any private artifact metadata."""
        rows = self._connection.execute(
            _SELECT_CASE_ARTIFACT_STATUSES,
            {"organization_id": self._organization_id, "case_id": case_id},
        ).mappings()
        return tuple(
            ArtifactStatus(
                artifact_id=row["artifact_id"],
                artifact_kind=row["artifact_kind"],
                status=row["status"],
                status_updated_at=row["status_updated_at"],
            )
            for row in rows
        )


_SELECT_CASE_ARTIFACT_STATUSES = text(
    """
    SELECT artifact_id, artifact_kind, 'upload_pending' AS status,
           created_at AS status_updated_at
      FROM artifact_upload_intents
     WHERE organization_id = :organization_id
       AND case_id = :case_id
       AND status = 'pending'
    UNION ALL
    SELECT artifacts.id AS artifact_id, artifacts.artifact_kind,
           COALESCE(
             'processing_' || latest_event.state,
             'verified_awaiting_processing'
           ) AS status,
           COALESCE(latest_event.occurred_at, artifacts.created_at) AS status_updated_at
      FROM artifacts
      LEFT JOIN LATERAL (
        SELECT state, occurred_at
          FROM artifact_processing_events
         WHERE organization_id = artifacts.organization_id
           AND artifact_id = artifacts.id
         ORDER BY occurred_at DESC, id DESC
         LIMIT 1
      ) AS latest_event ON TRUE
     WHERE artifacts.organization_id = :organization_id
       AND artifacts.case_id = :case_id
     ORDER BY status_updated_at ASC, artifact_id ASC
    """
)
