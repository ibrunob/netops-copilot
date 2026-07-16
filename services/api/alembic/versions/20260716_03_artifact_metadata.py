"""Persist immutable, tenant-scoped artifact metadata.

Revision ID: 20260716_03
Revises: 20260715_02
Create Date: 2026-07-16

Artifact bytes remain in the object store.  This table deliberately retains
only the immutable metadata needed to authorize, verify, retain, and audit an
artifact: its tenant/case ownership, immutable storage locator and digest,
content classification, encryption reference, and retention deadline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260716_03"
down_revision: str | None = "20260715_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only artifact metadata ledger and tenant boundary."""
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_kind", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("encryption_key_reference", sa.Text(), nullable=False),
        sa.Column("created_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column(
            "retention_until",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_id"],
            ["cases.organization_id", "cases.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(btrim(artifact_kind)) > 0", name="ck_artifacts_kind_not_blank"),
        sa.CheckConstraint(
            "classification IN ('raw', 'redacted', 'derived')",
            name="ck_artifacts_classification",
        ),
        sa.CheckConstraint(
            "length(btrim(storage_key)) > 0", name="ck_artifacts_storage_key_not_blank"
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifacts_sha256"),
        sa.CheckConstraint("byte_size >= 0", name="ck_artifacts_byte_size_non_negative"),
        sa.CheckConstraint(
            "length(btrim(content_type)) > 0", name="ck_artifacts_content_type_not_blank"
        ),
        sa.CheckConstraint(
            "length(btrim(encryption_key_reference)) > 0",
            name="ck_artifacts_encryption_key_reference_not_blank",
        ),
        sa.CheckConstraint(
            "retention_until >= created_at", name="ck_artifacts_retention_not_before_creation"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_artifacts_organization_id"),
        sa.UniqueConstraint(
            "organization_id", "storage_key", name="uq_artifacts_organization_storage_key"
        ),
    )
    op.create_index(
        "ix_artifacts_organization_case_created_at",
        "artifacts",
        ["organization_id", "case_id", "created_at", "id"],
    )
    op.create_index(
        "ix_artifacts_organization_retention_until",
        "artifacts",
        ["organization_id", "retention_until", "id"],
    )
    op.execute(
        """
        CREATE TRIGGER trg_artifacts_immutable
          BEFORE UPDATE OR DELETE ON public.artifacts
          FOR EACH ROW EXECUTE FUNCTION public.netops_reject_immutable_history_mutation();

        REVOKE ALL ON TABLE public.artifacts FROM PUBLIC;
        ALTER TABLE public.artifacts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.artifacts FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_artifacts ON public.artifacts
          FOR ALL TO netops_app
          USING (organization_id = public.netops_current_organization_id())
          WITH CHECK (organization_id = public.netops_current_organization_id());
        GRANT SELECT, INSERT ON TABLE public.artifacts TO netops_app;
        """
    )


def downgrade() -> None:
    """Remove M4 artifact metadata without altering the case spine."""
    op.execute("DROP TRIGGER IF EXISTS trg_artifacts_immutable ON public.artifacts;")
    op.drop_index("ix_artifacts_organization_retention_until", table_name="artifacts")
    op.drop_index("ix_artifacts_organization_case_created_at", table_name="artifacts")
    op.drop_table("artifacts")
