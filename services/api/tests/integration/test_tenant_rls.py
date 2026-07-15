"""Adversarial PostgreSQL RLS coverage for the tenant persistence boundary.

Run only through ``make test-rls``. The test reads explicit DSNs so normal unit
tests neither start Docker nor risk pointing at a developer's core database.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from netops_api.core.database import TenantDatabase
from netops_api.core.tenant_context import tenant_transaction

pytestmark = pytest.mark.integration

ORGANIZATION_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667788")
ORGANIZATION_B = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667789")
ASSET_A = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778a")
ASSET_B = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778b")
FORCED_OWNER_ROLE = "netops_rls_force_owner"


def _required_url(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required; run this test through `make test-rls`.")
    return value


@pytest.fixture(scope="module")
def owner_engine() -> Iterator[Engine]:
    """Provide the isolated migration-owner connection, never a core DB URL."""
    engine = create_engine(_required_url("NETOPS_RLS_OWNER_DATABASE_URL"))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def application_engine() -> Iterator[Engine]:
    """Connect exactly as the runtime ``netops_app`` role does."""
    engine = create_engine(_required_url("NETOPS_RLS_TEST_DATABASE_URL"))
    try:
        yield engine
    finally:
        engine.dispose()


def _owner_identifier(owner_engine: Engine) -> str:
    username = make_url(str(owner_engine.url)).username
    if username is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", username):
        raise AssertionError("test migration owner must be a simple PostgreSQL identifier")
    return username


@contextmanager
def _prepared_tenants(owner_engine: Engine) -> Iterator[None]:
    """Insert independent tenant fixtures and always restore test-table ownership."""
    owner_identifier = _owner_identifier(owner_engine)
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_events"))
        connection.execute(text("DELETE FROM assets"))
        connection.execute(text("DELETE FROM organization_settings"))
        connection.execute(text("DELETE FROM memberships"))
        connection.execute(text("DELETE FROM organizations"))
        connection.execute(
            text(
                "INSERT INTO organizations (id, slug, display_name) "
                "VALUES (:id_a, 'tenant-a', 'Tenant A'), (:id_b, 'tenant-b', 'Tenant B')"
            ),
            {"id_a": ORGANIZATION_A, "id_b": ORGANIZATION_B},
        )
        connection.execute(
            text(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'netops_rls_force_owner') "
                "THEN CREATE ROLE netops_rls_force_owner NOLOGIN NOINHERIT NOBYPASSRLS; "
                "END IF; END $$;"
            )
        )
        connection.execute(text(f"GRANT {FORCED_OWNER_ROLE} TO {owner_identifier}"))
    try:
        yield
    finally:
        # The temporary ownership change below proves FORCE ROW LEVEL SECURITY
        # affects an otherwise privileged table owner. Restore it even when an
        # assertion fails so later migration tests retain their normal owner.
        with owner_engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE assets OWNER TO {owner_identifier}"))
            connection.execute(text("DELETE FROM audit_events"))
            connection.execute(text("DELETE FROM assets"))
            connection.execute(text("DELETE FROM organization_settings"))
            connection.execute(text("DELETE FROM memberships"))
            connection.execute(text("DELETE FROM organizations"))


def test_tenant_rls_denies_cross_organization_access_and_connection_leaks(
    owner_engine: Engine, application_engine: Engine
) -> None:
    """Exercise RLS with direct SQL and the production tenant connection boundary."""
    database = TenantDatabase(application_engine)

    with _prepared_tenants(owner_engine):
        with database.tenant_connection(ORGANIZATION_A) as connection:
            connection.execute(
                text(
                    "INSERT INTO assets (id, organization_id, name) "
                    "VALUES (:id, :organization_id, 'edge-a')"
                ),
                {"id": ASSET_A, "organization_id": ORGANIZATION_A},
            )
            assert connection.scalars(text("SELECT id FROM assets")).all() == [ASSET_A]

        # A second tenant cannot read, mutate, or delete tenant A's row. RLS
        # hides existing rows for UPDATE/DELETE and rejects a forged tenant ID
        # on INSERT through the policy's WITH CHECK predicate.
        with database.tenant_connection(ORGANIZATION_B) as connection:
            assert connection.scalars(text("SELECT id FROM assets")).all() == []
            assert connection.execute(
                text("UPDATE assets SET name = 'forged' WHERE id = :id"), {"id": ASSET_A}
            ).rowcount == 0
            assert connection.execute(
                text("DELETE FROM assets WHERE id = :id"), {"id": ASSET_A}
            ).rowcount == 0
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO assets (id, organization_id, name) "
                        "VALUES (:id, :organization_id, 'forged-cross-tenant')"
                    ),
                    {"id": ASSET_B, "organization_id": ORGANIZATION_A},
                )

        # No context, malformed context, and a returned pooled connection all
        # fail closed. The explicit direct-SQL blocks catch accidental future
        # bypasses of TenantDatabase in a repository implementation.
        with application_engine.connect() as connection:
            with connection.begin():
                assert connection.scalars(text("SELECT id FROM assets")).all() == []
            with connection.begin():
                connection.execute(
                    text("SELECT set_config('app.organization_id', 'not-a-uuid', true)")
                )
                assert connection.scalars(text("SELECT id FROM assets")).all() == []
            with tenant_transaction(connection, ORGANIZATION_A):
                assert connection.scalars(text("SELECT id FROM assets")).all() == [ASSET_A]
            with connection.begin():
                assert connection.scalar(
                    text("SELECT current_setting('app.organization_id', true)")
                ) in {None, ""}
                assert connection.scalars(text("SELECT id FROM assets")).all() == []

        # FORCE ROW LEVEL SECURITY must constrain even the table owner. The
        # test temporarily assigns the real assets table to a non-bypass role;
        # it has ownership privileges but no policy grant, so it sees no rows.
        with owner_engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE assets OWNER TO {FORCED_OWNER_ROLE}"))
            connection.execute(text(f"SET LOCAL ROLE {FORCED_OWNER_ROLE}"))
            assert connection.scalar(text("SELECT count(*) FROM assets")) == 0
