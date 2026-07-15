"""Typed configuration loaded only at the application boundary."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported deployment environments."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ServiceEndpoint(BaseModel):
    """A non-secret network location for an external platform service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


class DependencyEndpoints(BaseModel):
    """Named service endpoints used by future infrastructure adapters.

    These settings describe locations only. Credentials stay in their dedicated secret
    providers when those integrations are introduced, rather than in this shared config.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    postgres: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="postgres", port=5432)
    )
    redis: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="redis", port=6379)
    )
    minio: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="minio", port=9000)
    )
    temporal: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="temporal", port=7233)
    )
    keycloak: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="keycloak", port=8080)
    )


class Settings(BaseSettings):
    """Runtime settings for the API service.

    Settings are prefixed with ``NETOPS_`` to avoid collisions in a shared local stack.
    No secret values are represented until their owning integration is implemented.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="NETOPS_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    service_name: str = "netops-api"
    api_version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False
    docs_enabled: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    dependencies: DependencyEndpoints = Field(default_factory=DependencyEndpoints)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached environment-backed settings for the running process."""
    return Settings()
