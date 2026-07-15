from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from netops_api.core.tenant_context import tenant_transaction


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.transaction_entered = False

    @contextmanager
    def begin(self):  # type: ignore[no-untyped-def]
        self.transaction_entered = True
        yield self

    def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
        self.calls.append((str(statement), parameters))


def test_tenant_context_is_set_locally_inside_a_transaction() -> None:
    connection = FakeConnection()
    organization_id = UUID("018f0b3c-5e8a-7f0a-8ac4-334455667788")

    with tenant_transaction(connection, organization_id) as scoped_connection:
        assert scoped_connection is connection

    assert connection.transaction_entered is True
    assert connection.calls == [
        (
            "SELECT set_config('app.organization_id', :organization_id, true)",
            {"organization_id": str(organization_id)},
        )
    ]
