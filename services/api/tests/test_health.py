from __future__ import annotations

import uuid
from dataclasses import replace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from netops_api.core.database import READINESS_QUERY_TIMEOUT_MS, TenantDatabase
from netops_api.main import get_application_dependencies


class StubReadinessDatabase:
    """A persistence boundary stand-in for HTTP readiness tests."""

    def __init__(self, failure: SQLAlchemyError | None = None) -> None:
        self.failure = failure
        self.calls = 0

    def check_readiness(self) -> None:
        self.calls += 1
        if self.failure is not None:
            raise self.failure


def test_liveness_includes_service_metadata_and_correlation_id(client: TestClient) -> None:
    correlation_id = str(uuid.uuid4())

    response = client.get("/healthz", headers={"X-Correlation-ID": correlation_id})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "netops-api-test",
        "version": "0.1.0",
    }
    assert response.headers["X-Correlation-ID"] == correlation_id


def test_invalid_correlation_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert uuid.UUID(response.headers["X-Correlation-ID"])


def test_readiness_reports_application_state_without_a_configured_database(
    client: TestClient,
) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["components"] == {"application": "ready"}


def test_readiness_checks_the_configured_database(
    app: FastAPI,
    client: TestClient,
) -> None:
    database = StubReadinessDatabase()
    app.state.dependencies = replace(get_application_dependencies(app), database=database)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["components"] == {"application": "ready", "database": "ready"}
    assert database.calls == 1


def test_readiness_hides_database_failure_details_and_retains_request_context(
    app: FastAPI,
    client: TestClient,
) -> None:
    failure = OperationalError(
        "SELECT 1",
        {},
        Exception("password=do-not-disclose"),
    )
    database = StubReadinessDatabase(failure)
    app.state.dependencies = replace(get_application_dependencies(app), database=database)
    correlation_id = str(uuid.uuid4())
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

    response = client.get(
        "/readyz",
        headers={"X-Correlation-ID": correlation_id, "traceparent": traceparent},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "persistence_unavailable",
        "message": "The application database is temporarily unavailable.",
        "request_id": correlation_id,
        "details": None,
    }
    assert "do-not-disclose" not in response.text
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.headers["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")
    assert response.headers["traceparent"].endswith("-01")
    assert database.calls == 1


def test_database_readiness_uses_a_transaction_local_timeout_and_probe() -> None:
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalar.return_value = 1
    database = TenantDatabase(engine=engine)

    database.check_readiness()

    assert len(connection.execute.call_args_list) == 1
    timeout_call = connection.execute.call_args_list[0]
    assert str(timeout_call.args[0]) == "SELECT set_config('statement_timeout', :timeout, true)"
    assert timeout_call.args[1] == {"timeout": f"{READINESS_QUERY_TIMEOUT_MS}ms"}
    assert str(connection.scalar.call_args.args[0]) == "SELECT 1"


def test_not_found_uses_typed_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    body = response.json()

    assert response.status_code == 404
    assert body["error"]["code"] == "http_error"
    assert body["error"]["request_id"] == response.headers["X-Correlation-ID"]
