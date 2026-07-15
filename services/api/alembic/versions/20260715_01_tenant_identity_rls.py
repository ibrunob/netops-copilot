"""Create tenant identity tables and enforce database-level tenant isolation.

Revision ID: 20260715_01
Revises:
Create Date: 2026-07-15

The migration owner is intentionally distinct from ``netops_app``. Runtime
connections must assume the latter role and set ``app.organization_id`` with
``SET LOCAL`` inside each transaction. This makes an unset or malformed tenant
context fail closed and prevents one pooled transaction from leaking a prior
tenant's context into the next request.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260715_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_TABLES = (
    "organizations",
    "memberships",
    "assets",
    "organization_settings",
    "audit_events",
)


def upgrade() -> None:
    """Create foundational identity records and fail-closed RLS policies."""
    application_password = os.environ.get("NETOPS_APPLICATION_DB_PASSWORD")
    if not application_password:
        raise RuntimeError("NETOPS_APPLICATION_DB_PASSWORD is required for the application role.")
    quoted_password = application_password.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'netops_app') THEN
            CREATE ROLE netops_app LOGIN NOINHERIT NOBYPASSRLS PASSWORD '{quoted_password}';
          END IF;
        END
        $$;
        ALTER ROLE netops_app LOGIN NOINHERIT NOBYPASSRLS PASSWORD '{quoted_password}';
        """
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("oidc_subject", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "memberships",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "role IN ('org_admin', 'operator', 'approver', 'auditor', "
            "'integration_admin', 'platform_admin')",
            name="ck_memberships_role",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organization_id", "user_id"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "name", name="uq_assets_organization_name"),
        sa.UniqueConstraint("organization_id", "id", name="uq_assets_organization_id"),
    )
    op.create_table(
        "organization_settings",
        sa.Column("organization_id", sa.Uuid(), primary_key=True),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default=sa.text("365")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "retention_days >= 1 AND retention_days <= 3650",
            name="ck_settings_retention_days",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_audit_events_organization_occurred_at",
        "audit_events",
        ["organization_id", "occurred_at"],
    )

    op.execute(
        """
        CREATE FUNCTION public.netops_current_organization_id()
        RETURNS uuid
        LANGUAGE plpgsql
        STABLE
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $$
        DECLARE
          raw_organization_id text := current_setting('app.organization_id', true);
        BEGIN
          IF raw_organization_id IS NULL
             OR raw_organization_id !~* (
               '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
               '[0-9a-f]{4}-[0-9a-f]{12}$'
             )
          THEN
            RETURN NULL;
          END IF;
          RETURN raw_organization_id::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
          RETURN NULL;
        END
        $$;
        REVOKE ALL ON FUNCTION public.netops_current_organization_id() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.netops_current_organization_id() TO netops_app;
        GRANT USAGE ON SCHEMA public TO netops_app;
        """
    )
    for table in TENANT_TABLES:
        tenant_column = "id" if table == "organizations" else "organization_id"
        policy_name = f"tenant_isolation_{table}"
        op.execute(
            f"""
            REVOKE ALL ON TABLE public.{table} FROM PUBLIC;
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} TO netops_app;
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {policy_name} ON public.{table}
              FOR ALL TO netops_app
              USING ({tenant_column} = public.netops_current_organization_id())
              WITH CHECK ({tenant_column} = public.netops_current_organization_id());
            """
        )


def downgrade() -> None:
    """Drop the tenant schema and its dedicated non-owner application role."""
    op.drop_index("ix_audit_events_organization_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("organization_settings")
    op.drop_table("assets")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("organizations")
    op.execute("DROP FUNCTION IF EXISTS public.netops_current_organization_id();")
    op.execute("DROP ROLE IF EXISTS netops_app;")
