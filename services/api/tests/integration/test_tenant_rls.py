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
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from netops_api.core.database import TenantDatabase
from netops_api.core.tenant_context import tenant_transaction

pytestmark = pytest.mark.integration

ORGANIZATION_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667788")
ORGANIZATION_B = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667789")
ASSET_A = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778a")
ASSET_B = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778b")
CASE_A = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778c")
CASE_B = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778d")
INPUT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778e")
TRANSITION_A = UUID("018f0b3c-5e8a-7f0a-8ac4-33445566778f")
INVALID_TRANSITION = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667790")
CASE_CREATED_EVENT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667791")
CASE_EVENT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667792")
OUTBOX_CREATED_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667793")
OUTBOX_EVENT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667794")
ACTOR_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667795")
CORRELATION_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667796")
AUDIT_EVENT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667797")
ARTIFACT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667798")
ARTIFACT_PROCESSING_EVENT_A = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667799")
FORCED_OWNER_ROLE = "netops_rls_force_owner"
TENANT_PERSISTENCE_TABLES = (
    "cases",
    "case_inputs",
    "case_events",
    "case_transitions",
    "outbox_events",
    "consumer_inbox",
    "audit_events",
    "artifacts",
    "artifact_upload_intents",
    "artifact_processing_events",
)


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


def _clear_case_spine(connection: Connection) -> None:
    """Clear isolated test fixtures without weakening append-only triggers."""
    connection.execute(
        text(
            "TRUNCATE TABLE artifact_processing_events, artifact_upload_intents, artifacts, "
            "consumer_inbox, "
            "outbox_events, case_events, "
            "case_transitions, case_inputs, cases, audit_events"
        )
    )


@contextmanager
def _prepared_tenants(owner_engine: Engine) -> Iterator[None]:
    """Insert independent tenant fixtures and always restore test-table ownership."""
    owner_identifier = _owner_identifier(owner_engine)
    with owner_engine.begin() as connection:
        _clear_case_spine(connection)
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
            _clear_case_spine(connection)
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
            assert (
                connection.execute(
                    text("UPDATE assets SET name = 'forged' WHERE id = :id"), {"id": ASSET_A}
                ).rowcount
                == 0
            )
            assert (
                connection.execute(
                    text("DELETE FROM assets WHERE id = :id"), {"id": ASSET_A}
                ).rowcount
                == 0
            )
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


def test_case_spine_schema_enforces_rls_and_append_only_history(
    owner_engine: Engine, application_engine: Engine
) -> None:
    """Exercise the M2 schema through the same isolated runtime role as production."""
    database = TenantDatabase(application_engine)

    with owner_engine.begin() as connection:
        relation_rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
                "WHERE nspname = 'public' AND relname IN "
                "('cases', 'case_inputs', 'case_events', 'case_transitions', "
                "'outbox_events', 'consumer_inbox', 'audit_events', 'artifacts', "
                "'artifact_upload_intents', 'artifact_processing_events')"
            )
        ).all()
        policies = set(
            connection.scalars(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = 'public' AND tablename IN "
                    "('cases', 'case_inputs', 'case_events', 'case_transitions', "
                    "'outbox_events', 'consumer_inbox', 'audit_events', 'artifacts', "
                    "'artifact_upload_intents', 'artifact_processing_events')"
                )
            ).all()
        )
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))

    assert revision == "20260716_06"
    assert {row.relname for row in relation_rows} == set(TENANT_PERSISTENCE_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in relation_rows)
    assert policies == {
        "tenant_isolation_cases",
        "tenant_isolation_case_inputs",
        "tenant_isolation_case_events",
        "tenant_isolation_case_transitions",
        "tenant_isolation_outbox_events",
        "tenant_isolation_consumer_inbox",
        "tenant_isolation_audit_events",
        "tenant_isolation_artifacts",
        "tenant_isolation_artifact_upload_intents",
        "tenant_isolation_artifact_processing_events",
    }

    with _prepared_tenants(owner_engine):
        with database.tenant_connection(ORGANIZATION_A) as connection:
            connection.execute(
                text(
                    "INSERT INTO cases "
                    "(id, organization_id, title, idempotency_key, request_sha256, correlation_id) "
                    "VALUES (:id, :organization_id, 'IPsec tunnel unavailable', 'case-create-a', "
                    ":request_sha256, :correlation_id)"
                ),
                {
                    "id": CASE_A,
                    "organization_id": ORGANIZATION_A,
                    "request_sha256": "c" * 64,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO case_inputs "
                    "(id, organization_id, case_id, input_kind, content_sha256, correlation_id) "
                    "VALUES (:id, :organization_id, :case_id, 'operator_note', :content_sha256, "
                    ":correlation_id)"
                ),
                {
                    "id": INPUT_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "content_sha256": "a" * 64,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO case_transitions "
                    "(id, organization_id, case_id, from_state, to_state, version, actor_id, "
                    "actor_kind, correlation_id) "
                    "VALUES (:id, :organization_id, :case_id, 'new', 'investigating', 1, "
                    ":actor_id, 'service', :correlation_id)"
                ),
                {
                    "id": TRANSITION_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "actor_id": ACTOR_A,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO case_events "
                    "(id, organization_id, case_id, event_type, aggregate_version, actor_id, "
                    "correlation_id) "
                    "VALUES (:id, :organization_id, :case_id, 'case.created.v1', 0, :actor_id, "
                    ":correlation_id)"
                ),
                {
                    "id": CASE_CREATED_EVENT_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "actor_id": ACTOR_A,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, organization_id, case_id, case_event_id, event_type, "
                    "aggregate_version, correlation_id) "
                    "VALUES (:id, :organization_id, :case_id, :case_event_id, "
                    "'case.created.v1', 0, :correlation_id)"
                ),
                {
                    "id": OUTBOX_CREATED_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "case_event_id": CASE_CREATED_EVENT_A,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO case_events "
                    "(id, organization_id, case_id, transition_id, event_type, aggregate_version, "
                    "actor_id, correlation_id) "
                    "VALUES (:id, :organization_id, :case_id, :transition_id, "
                    "'case.investigating.v1', 1, :actor_id, :correlation_id)"
                ),
                {
                    "id": CASE_EVENT_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "transition_id": TRANSITION_A,
                    "actor_id": ACTOR_A,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, organization_id, case_id, case_event_id, event_type, "
                    "aggregate_version, correlation_id) "
                    "VALUES (:id, :organization_id, :case_id, :case_event_id, "
                    "'case.investigating.v1', 1, :correlation_id)"
                ),
                {
                    "id": OUTBOX_EVENT_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "case_event_id": CASE_EVENT_A,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "UPDATE cases SET state = 'investigating', version = 1 "
                    "WHERE organization_id = :organization_id AND id = :case_id"
                ),
                {"organization_id": ORGANIZATION_A, "case_id": CASE_A},
            )
            connection.execute(
                text(
                    "INSERT INTO consumer_inbox "
                    "(organization_id, consumer_name, event_id, payload_sha256) "
                    "VALUES (:organization_id, 'case-projector', :event_id, :payload_sha256)"
                ),
                {
                    "organization_id": ORGANIZATION_A,
                    "event_id": OUTBOX_EVENT_A,
                    "payload_sha256": "b" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO audit_events "
                    "(id, organization_id, actor_subject, action, correlation_id) "
                    "VALUES (:id, :organization_id, 'service:case-spine-test', "
                    "'case.created', :correlation_id)"
                ),
                {
                    "id": AUDIT_EVENT_A,
                    "organization_id": ORGANIZATION_A,
                    "correlation_id": CORRELATION_A,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts "
                    "(id, organization_id, case_id, artifact_kind, classification, storage_key, "
                    "sha256, byte_size, content_type, encryption_key_reference, retention_until) "
                    "VALUES (:id, :organization_id, :case_id, 'network-config', 'raw', "
                    "'org-a/cases/case-a/config-a.enc', :sha256, 128, 'text/plain', "
                    "'local-kms-key-v1', CURRENT_TIMESTAMP + INTERVAL '30 days')"
                ),
                {
                    "id": ARTIFACT_A,
                    "organization_id": ORGANIZATION_A,
                    "case_id": CASE_A,
                    "sha256": "e" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO artifact_processing_events "
                    "(id, organization_id, artifact_id, attempt, state, processor, "
                    "processor_version, correlation_id, result_summary) "
                    "VALUES (:id, :organization_id, :artifact_id, 1, 'quarantined', "
                    "'malware-scanner', 'v1', :correlation_id, CAST(:summary AS jsonb))"
                ),
                {
                    "id": ARTIFACT_PROCESSING_EVENT_A,
                    "organization_id": ORGANIZATION_A,
                    "artifact_id": ARTIFACT_A,
                    "correlation_id": CORRELATION_A,
                    "summary": '{"scan":"pending"}',
                },
            )
            connection.execute(
                text(
                    "INSERT INTO artifact_processing_events "
                    "(organization_id, artifact_id, attempt, state, processor, "
                    "processor_version, correlation_id) "
                    "VALUES (:organization_id, :artifact_id, 1, 'verified', "
                    "'malware-scanner', 'v1', :correlation_id)"
                ),
                {
                    "organization_id": ORGANIZATION_A,
                    "artifact_id": ARTIFACT_A,
                    "correlation_id": CORRELATION_A,
                },
            )

        with database.tenant_connection(ORGANIZATION_B) as connection:
            for table in TENANT_PERSISTENCE_TABLES:
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO cases "
                        "(id, organization_id, title, idempotency_key, request_sha256, "
                        "correlation_id) "
                        "VALUES (:id, :organization_id, 'forged cross-tenant case', "
                        "'forged-case-b', :request_sha256, :correlation_id)"
                    ),
                    {
                        "id": CASE_B,
                        "organization_id": ORGANIZATION_A,
                        "request_sha256": "d" * 64,
                        "correlation_id": CORRELATION_A,
                    },
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO artifact_processing_events "
                        "(organization_id, artifact_id, attempt, state, processor, "
                        "processor_version, correlation_id) "
                        "VALUES (:organization_id, :artifact_id, 2, 'verified', "
                        "'malware-scanner', 'v1', :correlation_id)"
                    ),
                    {
                        "organization_id": ORGANIZATION_A,
                        "artifact_id": ARTIFACT_A,
                        "correlation_id": CORRELATION_A,
                    },
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO artifacts "
                        "(organization_id, case_id, artifact_kind, classification, storage_key, "
                        "sha256, byte_size, content_type, encryption_key_reference, "
                        "retention_until) "
                        "VALUES (:organization_id, :case_id, 'network-config', 'raw', "
                        "'forged/object.enc', :sha256, 1, 'text/plain', 'forged-key', "
                        "CURRENT_TIMESTAMP + INTERVAL '1 day')"
                    ),
                    {
                        "organization_id": ORGANIZATION_A,
                        "case_id": CASE_A,
                        "sha256": "f" * 64,
                    },
                )

        with database.tenant_connection(ORGANIZATION_A) as connection:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("UPDATE case_events SET event_type = 'forged' WHERE id = :id"),
                    {"id": CASE_EVENT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("UPDATE audit_events SET action = 'forged' WHERE id = :id"),
                    {"id": AUDIT_EVENT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("UPDATE artifacts SET storage_key = 'forged/object.enc' WHERE id = :id"),
                    {"id": ARTIFACT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM artifacts WHERE id = :id"),
                    {"id": ARTIFACT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE artifact_processing_events SET processor = 'forged' WHERE id = :id"
                    ),
                    {"id": ARTIFACT_PROCESSING_EVENT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM artifact_processing_events WHERE id = :id"),
                    {"id": ARTIFACT_PROCESSING_EVENT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO case_transitions "
                        "(id, organization_id, case_id, from_state, to_state, version, actor_id, "
                        "actor_kind, correlation_id, verification_note) "
                        "VALUES (:id, :organization_id, :case_id, 'new', 'resolved', 2, "
                        ":actor_id, 'human', :correlation_id, 'forged transition')"
                    ),
                    {
                        "id": INVALID_TRANSITION,
                        "organization_id": ORGANIZATION_A,
                        "case_id": CASE_A,
                        "actor_id": ACTOR_A,
                        "correlation_id": CORRELATION_A,
                    },
                )

        with pytest.raises(DBAPIError):
            with database.tenant_connection(ORGANIZATION_A) as connection:
                connection.execute(
                    text(
                        "UPDATE cases SET state = 'diagnosed', version = 2 "
                        "WHERE organization_id = :organization_id AND id = :case_id"
                    ),
                    {"organization_id": ORGANIZATION_A, "case_id": CASE_A},
                )

        with owner_engine.begin() as connection:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("UPDATE case_events SET event_type = 'forged' WHERE id = :id"),
                    {"id": CASE_EVENT_A},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE outbox_events SET payload = CAST(:payload AS jsonb) WHERE id = :id"
                    ),
                    {"id": OUTBOX_EVENT_A, "payload": '{"forged":true}'},
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("UPDATE audit_events SET action = 'forged' WHERE id = :id"),
                    {"id": AUDIT_EVENT_A},
                )
