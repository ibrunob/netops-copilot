"""Transaction-scoped database tenant context for Row-Level Security."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, text


@contextmanager
def tenant_transaction(connection: Connection, organization_id: UUID) -> Iterator[Connection]:
    """Bind an organization to one transaction using PostgreSQL ``SET LOCAL``.

    Callers obtain ``organization_id`` exclusively from a verified principal. The
    PostgreSQL setting is transaction-local, so a pooled connection cannot carry
    a tenant value into the next request once this block commits or rolls back.
    """
    with connection.begin():
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": str(organization_id)},
        )
        yield connection
