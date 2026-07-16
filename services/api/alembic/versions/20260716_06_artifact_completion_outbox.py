"""Permit multiple non-transition case facts at one aggregate version.

Revision ID: 20260716_06
Revises: 20260716_05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_06"
down_revision: str | None = "20260716_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep transition events unique while allowing repeated artifact facts."""
    op.drop_constraint("uq_case_events_case_type_version", "case_events", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_case_events_transition_id
          ON public.case_events (organization_id, transition_id)
          WHERE transition_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.uq_case_events_transition_id;")
    op.create_unique_constraint(
        "uq_case_events_case_type_version",
        "case_events",
        ["organization_id", "case_id", "event_type", "aggregate_version"],
    )
