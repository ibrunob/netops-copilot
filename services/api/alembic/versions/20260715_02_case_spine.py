"""Create the tenant-scoped case projection, immutable history, and outbox.

Revision ID: 20260715_02
Revises: 20260715_01
Create Date: 2026-07-15

The mutable ``cases`` projection is deliberately separated from immutable case
input, transition, event, and audit history. Every organization-owned table
uses the M1 fail-closed RLS function and the unprivileged ``netops_app`` role.
The outbox retains mutable delivery bookkeeping, while its business event
identity and payload remain immutable after insertion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260715_02"
down_revision: str | None = "20260715_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CASE_STATES = (
    "new",
    "investigating",
    "diagnosed",
    "fix_proposed",
    "needs_information",
    "confirmed",
    "resolved",
    "learned",
)

CASE_TABLES = (
    "cases",
    "case_inputs",
    "case_transitions",
    "case_events",
    "outbox_events",
    "consumer_inbox",
)


def _state_check(column: str) -> str:
    """Return a quoted SQL check for a persisted case-state column."""
    values = ", ".join(f"'{state}'" for state in CASE_STATES)
    return f"{column} IN ({values})"


def upgrade() -> None:
    """Create M2's tenant-isolated case persistence spine."""
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("created_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "asset_id"],
            ["assets.organization_id", "assets.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_cases_title_not_blank"),
        sa.CheckConstraint(
            "length(btrim(idempotency_key)) BETWEEN 1 AND 255",
            name="ck_cases_idempotency_key",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_cases_request_sha256",
        ),
        sa.CheckConstraint(_state_check("state"), name="ck_cases_state"),
        sa.CheckConstraint("version >= 0", name="ck_cases_version_non_negative"),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_cases_severity",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_cases_organization_id"),
    )
    op.create_index(
        "ix_cases_organization_state_updated_at",
        "cases",
        ["organization_id", "state", sa.text("updated_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_cases_organization_asset_updated_at",
        "cases",
        ["organization_id", "asset_id", sa.text("updated_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "uq_cases_organization_idempotency_key",
        "cases",
        ["organization_id", "idempotency_key"],
        unique=True,
    )

    op.create_table(
        "case_inputs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("input_kind", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_id"],
            ["cases.organization_id", "cases.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(btrim(input_kind)) > 0", name="ck_case_inputs_kind_not_blank"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_case_inputs_content_sha256",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_case_inputs_organization_id"),
    )
    op.create_index(
        "ix_case_inputs_organization_case_created_at",
        "case_inputs",
        ["organization_id", "case_id", "created_at", "id"],
    )

    op.create_table(
        "case_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("knowledge_item_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_id"],
            ["cases.organization_id", "cases.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_state_check("from_state"), name="ck_case_transitions_from_state"),
        sa.CheckConstraint(_state_check("to_state"), name="ck_case_transitions_to_state"),
        sa.CheckConstraint("version > 0", name="ck_case_transitions_version_positive"),
        sa.CheckConstraint(
            "actor_kind IN ('human', 'service')",
            name="ck_case_transitions_actor_kind",
        ),
        sa.CheckConstraint(
            "(from_state = 'new' AND to_state = 'investigating') "
            "OR (from_state = 'investigating' AND to_state IN ('diagnosed', 'needs_information')) "
            "OR (from_state = 'needs_information' AND to_state = 'investigating') "
            "OR (from_state = 'diagnosed' AND to_state = 'fix_proposed') "
            "OR (from_state = 'fix_proposed' AND to_state = 'confirmed') "
            "OR (from_state = 'confirmed' AND to_state = 'resolved') "
            "OR (from_state = 'resolved' AND to_state = 'learned')",
            name="ck_case_transitions_allowed_edge",
        ),
        sa.CheckConstraint(
            "to_state <> 'confirmed' OR (actor_kind = 'human' AND approval_id IS NOT NULL)",
            name="ck_case_transitions_confirmation",
        ),
        sa.CheckConstraint(
            "to_state <> 'resolved' OR (actor_kind = 'human' "
            "AND verification_note IS NOT NULL AND length(btrim(verification_note)) > 0)",
            name="ck_case_transitions_resolution",
        ),
        sa.CheckConstraint(
            "to_state <> 'learned' OR (actor_kind = 'service' AND knowledge_item_id IS NOT NULL)",
            name="ck_case_transitions_learning",
        ),
        sa.CheckConstraint(
            "to_state <> 'needs_information' OR (note IS NOT NULL AND length(btrim(note)) > 0)",
            name="ck_case_transitions_needs_information",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_case_transitions_organization_id"),
        sa.UniqueConstraint("case_id", "version", name="uq_case_transitions_case_version"),
    )
    op.create_index(
        "ix_case_transitions_organization_case_occurred_at",
        "case_transitions",
        ["organization_id", "case_id", "occurred_at", "id"],
    )

    op.create_table(
        "case_events",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("transition_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_id"],
            ["cases.organization_id", "cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "transition_id"],
            ["case_transitions.organization_id", "case_transitions.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(btrim(event_type)) > 0", name="ck_case_events_type_not_blank"),
        sa.CheckConstraint("aggregate_version >= 0", name="ck_case_events_version_non_negative"),
        sa.UniqueConstraint("organization_id", "id", name="uq_case_events_organization_id"),
        sa.UniqueConstraint(
            "case_id",
            "event_type",
            "aggregate_version",
            name="uq_case_events_case_type_version",
        ),
    )
    op.create_index(
        "ix_case_events_organization_case_occurred_at",
        "case_events",
        ["organization_id", "case_id", "occurred_at", "id"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_id"],
            ["cases.organization_id", "cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_event_id"],
            ["case_events.organization_id", "case_events.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(btrim(event_type)) > 0", name="ck_outbox_events_type_not_blank"),
        sa.CheckConstraint("aggregate_version >= 0", name="ck_outbox_events_version_non_negative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_non_negative"),
        sa.UniqueConstraint("organization_id", "id", name="uq_outbox_events_organization_id"),
        sa.UniqueConstraint("case_event_id", name="uq_outbox_events_case_event"),
    )
    op.create_index(
        "ix_outbox_events_unpublished_available_at",
        "outbox_events",
        ["available_at", "created_at", "id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_index(
        "ix_outbox_events_organization_case_created_at",
        "outbox_events",
        ["organization_id", "case_id", "created_at", "id"],
    )

    op.create_table(
        "consumer_inbox",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_name", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "length(btrim(consumer_name)) > 0",
            name="ck_consumer_inbox_consumer_not_blank",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_consumer_inbox_payload_sha256",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_consumer_inbox_organization_id"),
        sa.UniqueConstraint(
            "organization_id",
            "consumer_name",
            "event_id",
            name="uq_consumer_inbox_deduplication",
        ),
    )
    op.create_index(
        "ix_consumer_inbox_organization_processed_at",
        "consumer_inbox",
        ["organization_id", "processed_at", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION public.netops_reject_immutable_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only and cannot be modified', TG_TABLE_NAME
            USING ERRCODE = '55000';
        END
        $$;

        CREATE TRIGGER trg_case_inputs_immutable
          BEFORE UPDATE OR DELETE ON public.case_inputs
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();
        CREATE TRIGGER trg_case_transitions_immutable
          BEFORE UPDATE OR DELETE ON public.case_transitions
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();
        CREATE TRIGGER trg_case_events_immutable
          BEFORE UPDATE OR DELETE ON public.case_events
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();
        CREATE TRIGGER trg_audit_events_immutable
          BEFORE UPDATE OR DELETE ON public.audit_events
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();
        CREATE TRIGGER trg_consumer_inbox_immutable
          BEFORE UPDATE OR DELETE ON public.consumer_inbox
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();

        CREATE FUNCTION public.netops_guard_outbox_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'outbox events cannot be deleted'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
             OR NEW.case_id IS DISTINCT FROM OLD.case_id
             OR NEW.case_event_id IS DISTINCT FROM OLD.case_event_id
             OR NEW.event_type IS DISTINCT FROM OLD.event_type
             OR NEW.aggregate_version IS DISTINCT FROM OLD.aggregate_version
             OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
             OR NEW.payload IS DISTINCT FROM OLD.payload
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'outbox business fields are immutable'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.attempt_count < OLD.attempt_count THEN
            RAISE EXCEPTION 'outbox attempt count cannot decrease'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.published_at IS NOT NULL
             AND NEW.published_at IS DISTINCT FROM OLD.published_at THEN
            RAISE EXCEPTION 'published outbox event cannot be unpublished or republished'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_outbox_events_business_fields_immutable
          BEFORE UPDATE OR DELETE ON public.outbox_events
          FOR EACH ROW EXECUTE FUNCTION public.netops_guard_outbox_event_mutation();

        CREATE FUNCTION public.netops_assert_case_projection_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.state <> 'new' OR NEW.version <> 0 THEN
              RAISE EXCEPTION 'new case projection must begin at new version 0'
                USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
              SELECT 1
              FROM public.case_events AS event
              JOIN public.outbox_events AS outbox
                ON outbox.organization_id = event.organization_id
               AND outbox.case_event_id = event.id
              WHERE event.organization_id = NEW.organization_id
                AND event.case_id = NEW.id
                AND event.event_type = 'case.created.v1'
                AND event.aggregate_version = 0
                AND event.transition_id IS NULL
                AND outbox.case_id = NEW.id
                AND outbox.aggregate_version = 0
                AND outbox.event_type = event.event_type
            ) THEN
              RAISE EXCEPTION 'case creation requires an immutable creation event and outbox row'
                USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
          END IF;

          IF (NEW.state IS DISTINCT FROM OLD.state)
             <> (NEW.version IS DISTINCT FROM OLD.version) THEN
            RAISE EXCEPTION 'case state and version must change together'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state THEN
            IF NEW.version <> OLD.version + 1 THEN
              RAISE EXCEPTION 'case version must advance exactly once per transition'
                USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
              SELECT 1
              FROM public.case_transitions AS transition
              JOIN public.case_events AS event
                ON event.organization_id = transition.organization_id
               AND event.case_id = transition.case_id
               AND event.transition_id = transition.id
              JOIN public.outbox_events AS outbox
                ON outbox.organization_id = event.organization_id
               AND outbox.case_event_id = event.id
              WHERE transition.organization_id = NEW.organization_id
                AND transition.case_id = NEW.id
                AND transition.from_state = OLD.state
                AND transition.to_state = NEW.state
                AND transition.version = NEW.version
                AND event.aggregate_version = NEW.version
                AND outbox.case_id = NEW.id
                AND outbox.aggregate_version = NEW.version
                AND outbox.event_type = event.event_type
            ) THEN
              RAISE EXCEPTION
                'case projection transition requires matching immutable history and outbox'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NULL;
        END
        $$;

        CREATE CONSTRAINT TRIGGER trg_cases_creation_requires_history
          AFTER INSERT ON public.cases
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION public.netops_assert_case_projection_history();
        CREATE CONSTRAINT TRIGGER trg_cases_transition_requires_history
          AFTER UPDATE ON public.cases
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION public.netops_assert_case_projection_history();
        """
    )

    for table in CASE_TABLES:
        op.execute(
            f"""
            REVOKE ALL ON TABLE public.{table} FROM PUBLIC;
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation_{table} ON public.{table}
              FOR ALL TO netops_app
              USING (organization_id = public.netops_current_organization_id())
              WITH CHECK (organization_id = public.netops_current_organization_id());
            """
        )

    op.execute(
        """
        REVOKE ALL ON TABLE public.audit_events FROM netops_app;
        GRANT SELECT, INSERT ON TABLE public.audit_events TO netops_app;
        GRANT SELECT, INSERT, UPDATE ON TABLE public.cases TO netops_app;
        GRANT SELECT, INSERT ON TABLE public.case_inputs TO netops_app;
        GRANT SELECT, INSERT ON TABLE public.case_transitions TO netops_app;
        GRANT SELECT, INSERT ON TABLE public.case_events TO netops_app;
        GRANT SELECT, INSERT, UPDATE ON TABLE public.outbox_events TO netops_app;
        GRANT SELECT, INSERT ON TABLE public.consumer_inbox TO netops_app;
        """
    )


def downgrade() -> None:
    """Remove the M2 case-spine schema without altering M1 tenant identity."""
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_consumer_inbox_immutable ON public.consumer_inbox;
        DROP TRIGGER IF EXISTS trg_outbox_events_business_fields_immutable ON public.outbox_events;
        DROP TRIGGER IF EXISTS trg_cases_transition_requires_history ON public.cases;
        DROP TRIGGER IF EXISTS trg_cases_creation_requires_history ON public.cases;
        DROP TRIGGER IF EXISTS trg_case_events_immutable ON public.case_events;
        DROP TRIGGER IF EXISTS trg_case_transitions_immutable ON public.case_transitions;
        DROP TRIGGER IF EXISTS trg_case_inputs_immutable ON public.case_inputs;
        DROP TRIGGER IF EXISTS trg_audit_events_immutable ON public.audit_events;
        """
    )
    op.drop_table("consumer_inbox")
    op.drop_table("outbox_events")
    op.drop_table("case_events")
    op.drop_table("case_transitions")
    op.drop_table("case_inputs")
    op.drop_table("cases")
    op.execute("DROP FUNCTION IF EXISTS public.netops_reject_immutable_history_mutation();")
    op.execute("DROP FUNCTION IF EXISTS public.netops_guard_outbox_event_mutation();")
    op.execute("DROP FUNCTION IF EXISTS public.netops_assert_case_projection_history();")
    op.execute(
        """
        REVOKE ALL ON TABLE public.audit_events FROM netops_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.audit_events TO netops_app;
        """
    )
