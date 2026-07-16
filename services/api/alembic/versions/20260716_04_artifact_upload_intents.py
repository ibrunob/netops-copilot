"""Persist tenant-scoped artifact upload declarations before completion.

Revision ID: 20260716_04
Revises: 20260716_03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260716_04"
down_revision: str | None = "20260716_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_upload_intents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_kind", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("created_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "case_id"],
            ["cases.organization_id", "cases.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("artifact_kind IN ('network-configuration', 'incident-audio')"),
        sa.CheckConstraint("classification = 'raw'"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 104857600"),
        sa.CheckConstraint("expires_at >= created_at"),
        sa.CheckConstraint("status IN ('pending', 'completed')"),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL)"
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_artifact_upload_intents_organization_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "storage_key", name="uq_artifact_upload_intents_storage_key"
        ),
    )
    op.create_index(
        "ix_artifact_upload_intents_organization_case_created_at",
        "artifact_upload_intents",
        ["organization_id", "case_id", "created_at", "id"],
    )
    op.execute(
        """
        REVOKE ALL ON TABLE public.artifact_upload_intents FROM PUBLIC;
        ALTER TABLE public.artifact_upload_intents ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.artifact_upload_intents FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_artifact_upload_intents ON public.artifact_upload_intents
          FOR ALL TO netops_app
          USING (organization_id = public.netops_current_organization_id())
          WITH CHECK (organization_id = public.netops_current_organization_id());
        GRANT SELECT, INSERT, UPDATE ON TABLE public.artifact_upload_intents TO netops_app;
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_upload_intents_organization_case_created_at",
        table_name="artifact_upload_intents",
    )
    op.drop_table("artifact_upload_intents")
