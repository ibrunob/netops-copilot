"""Typed, environment-backed configuration for the isolated worker process."""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environments supported by the worker."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


_TASK_QUEUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class WorkerSettings(BaseSettings):
    """Worker settings, including the explicit scoped database publisher opt-in."""

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
    outbox_publisher_enabled: bool = False
    database_url: SecretStr | None = None
    outbox_organization_id: UUID | None = None
    outbox_consumer_name: str = "case-event-receipt.v1"
    outbox_worker_id: str = "netops-worker"
    outbox_batch_size: int = Field(default=50, ge=1, le=100)
    outbox_lease_seconds: int = Field(default=60, ge=1, le=3_600)
    outbox_retry_seconds: int = Field(default=30, ge=1, le=3_600)
    outbox_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    clamav_enabled: bool = True
    clamav_address: str = "clamav:3310"
    clamav_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    clamav_read_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    clamav_max_scan_bytes: int = Field(default=104_857_600, ge=1, le=1_073_741_824)

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

    @field_validator("clamav_address")
    @classmethod
    def require_clamav_host_and_port(cls, value: str) -> str:
        """Reject URLs and malformed endpoints before a scanner opens a socket."""
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

    @field_validator("outbox_consumer_name", "outbox_worker_id")
    @classmethod
    def require_safe_outbox_identifier(cls, value: str) -> str:
        if not value.strip() or len(value) > 200:
            raise ValueError("must contain 1 to 200 non-blank characters")
        return value.strip()

    @field_validator("database_url", "outbox_organization_id", mode="before")
    @classmethod
    def blank_optional_outbox_setting_is_absent(cls, value: object) -> object:
        """Let Compose omit a scoped publisher setting with an empty expansion."""
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def require_scoped_database_for_enabled_publisher(self) -> WorkerSettings:
        if self.outbox_publisher_enabled and (
            self.database_url is None or self.outbox_organization_id is None
        ):
            raise ValueError(
                "database_url and outbox_organization_id are required when "
                "outbox publisher is enabled"
            )
        return self


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Return the one environment-backed configuration object for this process."""
    return WorkerSettings()
