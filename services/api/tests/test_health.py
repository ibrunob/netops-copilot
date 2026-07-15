from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


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


def test_readiness_is_explicit_about_current_dependency_scope(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["components"] == {"application": "ready"}


def test_not_found_uses_typed_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    body = response.json()

    assert response.status_code == 404
    assert body["error"]["code"] == "http_error"
    assert body["error"]["request_id"] == response.headers["X-Correlation-ID"]
