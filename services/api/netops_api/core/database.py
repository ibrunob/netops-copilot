"""Tenant-scoped SQLAlchemy boundary for HTTP and repository code."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

from netops_api.core.tenant_context import tenant_transaction


class TenantContextError(RuntimeError):
    """Raised when PostgreSQL did not retain the expected transaction tenant."""


@dataclass(frozen=True, slots=True)
class TenantDatabase:
    """Own the application connection pool and tenant transaction boundary.

    Routes and repositories must obtain connections through
    :meth:`tenant_connection`; opening an engine connection directly would skip
    the fail-closed RLS context required by every organization-owned table.
    """

    engine: Engine

    @classmethod
    def from_url(cls, database_url: str) -> TenantDatabase:
        """Build a PostgreSQL pool without connecting during application startup."""
        return cls(engine=create_engine(database_url, pool_pre_ping=True))

    @contextmanager
    def tenant_connection(self, organization_id: UUID) -> Iterator[Connection]:
        """Yield a connection whose current transaction is bound to one organization."""
        with self.engine.connect() as connection:
            with tenant_transaction(connection, organization_id) as scoped_connection:
                configured_organization_id = scoped_connection.scalar(
                    text("SELECT current_setting('app.organization_id', true)")
                )
                if configured_organization_id != str(organization_id):
                    raise TenantContextError(
                        "PostgreSQL tenant context was not bound to the verified organization."
                    )
                yield scoped_connection

    def dispose(self) -> None:
        """Release pool resources when the application stops."""
        self.engine.dispose()
