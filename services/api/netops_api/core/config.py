"""Typed configuration loaded only at the application boundary."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
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
    redis: ServiceEndpoint = Field(default_factory=lambda: ServiceEndpoint(host="redis", port=6379))
    minio: ServiceEndpoint = Field(default_factory=lambda: ServiceEndpoint(host="minio", port=9000))
    temporal: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="temporal", port=7233)
    )
    keycloak: ServiceEndpoint = Field(
        default_factory=lambda: ServiceEndpoint(host="keycloak", port=8080)
    )


class AuthSettings(BaseModel):
    """OIDC resource-server settings for the API.

    ``issuer`` is the public issuer embedded in tokens. ``jwks_url`` may use an
    internal service address so an API container does not need to route through
    a public load balancer to obtain the signing keys. The two values are
    deliberately separate; they must never be treated as interchangeable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str = "http://localhost:8080/realms/netops-dev"
    audience: str = "netops-api"
    jwks_url: str = "http://keycloak:8080/realms/netops-dev/protocol/openid-connect/certs"
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    organization_claim: str = "organization_id"
    asset_ids_claim: str = "asset_ids"

    @field_validator("issuer", "jwks_url")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        """Reject non-HTTP, fragment-bearing token authority URLs."""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.fragment:
            raise ValueError("must be an absolute HTTP(S) URL without a fragment")
        return value.rstrip("/")

    @field_validator("allowed_algorithms")
    @classmethod
    def require_supported_asymmetric_algorithms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Only accept asymmetric algorithms appropriate for an OIDC JWKS."""
        allowed = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})
        if not value or not set(value).issubset(allowed):
            raise ValueError("must contain one or more supported asymmetric JWT algorithms")
        return value

    @field_validator("organization_claim", "asset_ids_claim")
    @classmethod
    def require_claim_name(cls, value: str) -> str:
        """Prevent empty or whitespace-only claim mapping configuration."""
        if not value.strip():
            raise ValueError("must be a non-empty claim name")
        return value


class Settings(BaseSettings):
    """Runtime settings for the API service.

    Settings are prefixed with ``NETOPS_`` to avoid collisions in a shared local stack.
    Secrets remain with their owning integration; the optional Sentry DSN is held as
    a ``SecretStr`` because the telemetry integration owns its configuration.
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
    otel_traces_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=120, ge=1, le=10_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    database_url: SecretStr | None = None
    dependencies: DependencyEndpoints = Field(default_factory=DependencyEndpoints)
    auth: AuthSettings = Field(default_factory=AuthSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached environment-backed settings for the running process."""
    return Settings()
