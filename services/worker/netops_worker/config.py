"""Typed, environment-backed configuration for the isolated worker process."""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environments supported by the worker."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


_TASK_QUEUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class WorkerSettings(BaseSettings):
    """Worker-only settings; no API request or database credentials are accepted here."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="NETOPS_WORKER_",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    service_name: str = "netops-worker"
    service_version: str = "0.1.0"
    health_host: str = "0.0.0.0"
    health_port: int = Field(default=8081, ge=0, le=65535)
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    task_queue: str = "netops-platform"
    graceful_shutdown_seconds: int = Field(default=30, ge=1, le=300)
    otel_traces_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("temporal_address")
    @classmethod
    def require_temporal_host_and_port(cls, value: str) -> str:
        """Reject URLs and malformed endpoints before the worker opens a socket."""
        if "://" in value or value.count(":") != 1:
            raise ValueError("must be a host:port endpoint")
        host, port = value.rsplit(":", maxsplit=1)
        if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("must be a host:port endpoint")
        return value

    @field_validator("temporal_namespace")
    @classmethod
    def require_namespace(cls, value: str) -> str:
        """Reject blank namespaces because a worker must select its tenancy explicitly."""
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @field_validator("task_queue")
    @classmethod
    def require_task_queue(cls, value: str) -> str:
        """Keep the queue name valid and bounded for Temporal and operational logs."""
        if not _TASK_QUEUE_PATTERN.fullmatch(value):
            raise ValueError("must be a 1-256 character Temporal task queue name")
        return value


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Return the one environment-backed configuration object for this process."""
    return WorkerSettings()
