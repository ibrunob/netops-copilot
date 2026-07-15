from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.core.config import Environment, Settings
from netops_api.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(environment=Environment.TEST, service_name="netops-api-test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
