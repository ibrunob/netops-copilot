"""Record append-only, tenant-scoped artifact processing state.

Revision ID: 20260716_05
Revises: 20260716_04
Create Date: 2026-07-16

Artifact metadata is deliberately immutable, so processing progress is modeled
as a separate append-only ledger.  The only states which may unlock later
trust boundaries are ``verified`` and ``redacted``.  A raw artifact begins in
``quarantined`` and cannot be redacted or made available to a parser/model
without a successful verification event in the same processing attempt.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260716_05"
down_revision: str | None = "20260716_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the immutable processing ledger and enforce safe transitions."""
    op.create_table(
        "artifact_processing_events",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("processor", sa.Text(), nullable=False),
        sa.Column("processor_version", sa.Text(), nullable=False),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "artifact_id"],
            ["artifacts.organization_id", "artifacts.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_artifact_processing_events_attempt_positive"),
        sa.CheckConstraint(
            "state IN ('quarantined', 'verified', 'redacted', 'failed')",
            name="ck_artifact_processing_events_state",
        ),
        sa.CheckConstraint(
            "length(btrim(processor)) BETWEEN 1 AND 128",
            name="ck_artifact_processing_events_processor",
        ),
        sa.CheckConstraint(
            "length(btrim(processor_version)) BETWEEN 1 AND 128",
            name="ck_artifact_processing_events_processor_version",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z0-9][a-z0-9_.-]{0,127}$'",
            name="ck_artifact_processing_events_failure_code",
        ),
        sa.CheckConstraint(
            "(state = 'failed') = (failure_code IS NOT NULL)",
            name="ck_artifact_processing_events_failure_code_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_summary) = 'object' "
            "AND octet_length(result_summary::text) <= 8192",
            name="ck_artifact_processing_events_result_summary",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_artifact_processing_events_organization_id"
        ),
        sa.UniqueConstraint(
            "artifact_id", "attempt", "state", name="uq_artifact_processing_events_state_once"
        ),
    )
    op.create_index(
        "ix_artifact_processing_events_organization_artifact_attempt",
        "artifact_processing_events",
        ["organization_id", "artifact_id", "attempt", "occurred_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION public.netops_validate_artifact_processing_event()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          prior_state text;
          highest_attempt integer;
        BEGIN
          -- Serialize attempts for one artifact.  This prevents two workers from
          -- both accepting a first verification or redaction transition.
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.artifact_id::text, 0));

          SELECT max(attempt) INTO highest_attempt
            FROM public.artifact_processing_events
           WHERE artifact_id = NEW.artifact_id;

          IF NEW.state = 'quarantined' THEN
            IF highest_attempt IS NULL THEN
              IF NEW.attempt <> 1 THEN
                RAISE EXCEPTION 'first artifact processing attempt must be 1';
              END IF;
            ELSIF NEW.attempt <> highest_attempt + 1 THEN
              RAISE EXCEPTION 'new artifact processing attempts must increment by one';
            END IF;
            RETURN NEW;
          END IF;

          IF highest_attempt IS NULL OR NEW.attempt <> highest_attempt THEN
            RAISE EXCEPTION 'artifact processing state requires an active quarantined attempt';
          END IF;

          SELECT state INTO prior_state
            FROM public.artifact_processing_events
           WHERE artifact_id = NEW.artifact_id AND attempt = NEW.attempt
           ORDER BY occurred_at DESC, id DESC
           LIMIT 1;

          IF (NEW.state = 'verified' AND prior_state = 'quarantined')
             OR (NEW.state = 'redacted' AND prior_state = 'verified')
             OR (NEW.state = 'failed' AND prior_state IN ('quarantined', 'verified')) THEN
            RETURN NEW;
          END IF;

          RAISE EXCEPTION 'invalid artifact processing transition from % to %',
            prior_state, NEW.state;
        END
        $$;

        CREATE TRIGGER trg_artifact_processing_events_transition
          BEFORE INSERT ON public.artifact_processing_events
          FOR EACH ROW EXECUTE FUNCTION public.netops_validate_artifact_processing_event();
        CREATE TRIGGER trg_artifact_processing_events_immutable
          BEFORE UPDATE OR DELETE ON public.artifact_processing_events
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();

        REVOKE ALL ON TABLE public.artifact_processing_events FROM PUBLIC;
        ALTER TABLE public.artifact_processing_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.artifact_processing_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_artifact_processing_events
          ON public.artifact_processing_events FOR ALL TO netops_app
          USING (organization_id = public.netops_current_organization_id())
          WITH CHECK (organization_id = public.netops_current_organization_id());
        GRANT SELECT, INSERT ON TABLE public.artifact_processing_events TO netops_app;
        """
    )


def downgrade() -> None:
    """Remove the artifact processing ledger without modifying immutable artifacts."""
    op.execute(
        "DROP TRIGGER IF EXISTS trg_artifact_processing_events_transition "
        "ON public.artifact_processing_events;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_artifact_processing_events_immutable "
        "ON public.artifact_processing_events;"
    )
    op.execute("DROP FUNCTION IF EXISTS public.netops_validate_artifact_processing_event();")
    op.drop_index(
        "ix_artifact_processing_events_organization_artifact_attempt",
        table_name="artifact_processing_events",
    )
    op.drop_table("artifact_processing_events")
