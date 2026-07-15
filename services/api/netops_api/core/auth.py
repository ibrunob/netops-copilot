"""OIDC JWT verification and tenant-bound principal construction.

The API is an OIDC resource server. It never accepts an organization or asset
scope from request input: those values are derived only from a signature-
verified access token. The Keycloak development realm emits the claims
documented in ``services/api/AUTHENTICATION.md``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from netops_api.core.config import AuthSettings
from netops_api.domain.cases import CaseRole


class AuthenticationError(Exception):
    """A client-safe authentication failure.

    The API adapter maps this exception to a stable error response without
    returning parsing, signature, or key-rotation internals to callers.
    """

    def __init__(self, code: str = "invalid_token") -> None:
        self.code = code
        super().__init__(code)


class AuthenticationServiceError(Exception):
    """The API could not safely obtain signing material from the IdP."""


class AuthorizationError(Exception):
    """A verified principal lacks a required product permission or scope."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Minimal identity and scope that application code may trust.

    An empty ``asset_ids`` set is deliberately *not* an all-assets grant. A
    later repository or policy adapter must deny asset-scoped access until the
    requested asset is explicitly present in this set.
    """

    subject: str
    organization_id: UUID
    roles: frozenset[CaseRole]
    asset_ids: frozenset[UUID]
    issuer: str
    client_id: str | None

    def require_any_role(self, *roles: CaseRole) -> None:
        """Require one product role; platform administrators satisfy role gates."""
        allowed_roles = frozenset(roles) | {CaseRole.PLATFORM_ADMIN}
        if not self.roles.intersection(allowed_roles):
            raise AuthorizationError("insufficient_role")

    def require_organization(self, organization_id: UUID) -> None:
        """Ensure a repository operation is confined to the token's organization."""
        if self.organization_id != organization_id:
            raise AuthorizationError("organization_scope_denied")

    def require_asset(self, asset_id: UUID) -> None:
        """Ensure an asset is explicitly included in the verified token scope."""
        if asset_id not in self.asset_ids:
            raise AuthorizationError("asset_scope_denied")


class SigningKeyResolver(Protocol):
    """Resolve a JWT signing key by key identifier without trusting the token."""

    async def get_signing_key(self, key_id: str, jwt_module: Any) -> Any:
        """Return the public key represented by a trusted JWKS entry."""


@dataclass(slots=True)
class RemoteJwksResolver:
    """Small TTL cache around a remote JWKS with a forced rotation refresh."""

    jwks_url: str
    cache_ttl_seconds: int = 300
    timeout_seconds: float = 3.0
    _keys: dict[str, Mapping[str, object]] = field(default_factory=dict, init=False)
    _expires_at: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def get_signing_key(self, key_id: str, jwt_module: Any) -> Any:
        """Return an allowed key, refreshing once immediately for key rotation."""
        async with self._lock:
            if time.monotonic() >= self._expires_at:
                await self._refresh()

            jwk = self._keys.get(key_id)
            if jwk is None:
                await self._refresh()
                jwk = self._keys.get(key_id)
            if jwk is None:
                raise AuthenticationError()

        try:
            return jwt_module.PyJWK.from_dict(dict(jwk)).key
        except Exception as exc:  # pragma: no cover - library-specific malformed key branches
            raise AuthenticationServiceError("The signing key could not be loaded.") from exc

    async def _refresh(self) -> None:
        payload = await asyncio.to_thread(self._download_jwks)
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            raise AuthenticationServiceError("The signing-key response is malformed.")

        keys: dict[str, Mapping[str, object]] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, Mapping):
                raise AuthenticationServiceError("The signing-key response is malformed.")
            key_id = raw_key.get("kid")
            if not isinstance(key_id, str) or not key_id or key_id in keys:
                raise AuthenticationServiceError("The signing-key response is malformed.")
            keys[key_id] = raw_key

        self._keys = keys
        self._expires_at = time.monotonic() + self.cache_ttl_seconds

    def _download_jwks(self) -> Mapping[str, object]:
        request = Request(self.jwks_url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AuthenticationServiceError("The signing-key endpoint is unavailable.") from exc
        if not isinstance(payload, Mapping):
            raise AuthenticationServiceError("The signing-key response is malformed.")
        return payload


@dataclass(frozen=True, slots=True)
class JwtTokenVerifier:
    """Verify an OIDC access token then construct a typed tenant principal."""

    settings: AuthSettings
    key_resolver: SigningKeyResolver

    @classmethod
    def from_settings(cls, settings: AuthSettings) -> JwtTokenVerifier:
        """Create the production verifier backed by the configured JWKS endpoint."""
        return cls(settings=settings, key_resolver=RemoteJwksResolver(settings.jwks_url))

    async def verify(self, access_token: str) -> AuthenticatedPrincipal:
        """Verify signature and registered claims before interpreting product claims."""
        jwt_module = _load_pyjwt()
        try:
            header = jwt_module.get_unverified_header(access_token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if not isinstance(algorithm, str) or algorithm not in self.settings.allowed_algorithms:
                raise AuthenticationError()
            if not isinstance(key_id, str) or not key_id:
                raise AuthenticationError()

            signing_key = await self.key_resolver.get_signing_key(key_id, jwt_module)
            claims = jwt_module.decode(
                access_token,
                signing_key,
                algorithms=list(self.settings.allowed_algorithms),
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=self.settings.clock_skew_seconds,
            )
        except AuthenticationError:
            raise
        except AuthenticationServiceError:
            raise
        except Exception as exc:
            invalid_token_error = getattr(jwt_module, "InvalidTokenError", ())
            if invalid_token_error and isinstance(exc, invalid_token_error):
                raise AuthenticationError() from exc
            raise AuthenticationError() from exc

        if not isinstance(claims, Mapping):
            raise AuthenticationError()
        return principal_from_claims(claims, self.settings)


def principal_from_claims(
    claims: Mapping[str, object], settings: AuthSettings
) -> AuthenticatedPrincipal:
    """Parse the signed NetOps claims into the sole source of tenant scope."""
    subject = _required_string(claims, "sub")
    issuer = _required_string(claims, "iss")
    organization_id = _uuid_claim(claims, settings.organization_claim, required=True)
    assert organization_id is not None
    asset_ids = _uuid_list_claim(claims, settings.asset_ids_claim)
    roles = _realm_roles(claims)
    client_id = _optional_string(claims, "azp")

    return AuthenticatedPrincipal(
        subject=subject,
        organization_id=organization_id,
        roles=roles,
        asset_ids=asset_ids,
        issuer=issuer,
        client_id=client_id,
    )


def _load_pyjwt() -> Any:
    """Load the explicitly pinned JWT verifier lazily for clear startup failures."""
    try:
        return importlib.import_module("jwt")
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment packaging fault
        raise AuthenticationServiceError("JWT verification support is not installed.") from exc


def _required_string(claims: Mapping[str, object], claim_name: str) -> str:
    value = claims.get(claim_name)
    if not isinstance(value, str) or not value.strip():
        raise AuthenticationError()
    return value


def _optional_string(claims: Mapping[str, object], claim_name: str) -> str | None:
    value = claims.get(claim_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AuthenticationError()
    return value


def _uuid_claim(
    claims: Mapping[str, object], claim_name: str, *, required: bool
) -> UUID | None:
    value = claims.get(claim_name)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise AuthenticationError()
    try:
        return UUID(value)
    except ValueError as exc:
        raise AuthenticationError() from exc


def _uuid_list_claim(claims: Mapping[str, object], claim_name: str) -> frozenset[UUID]:
    value = claims.get(claim_name)
    if value is None:
        return frozenset()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise AuthenticationError()

    asset_ids: set[UUID] = set()
    for item in value:
        if not isinstance(item, str):
            raise AuthenticationError()
        try:
            asset_ids.add(UUID(item))
        except ValueError as exc:
            raise AuthenticationError() from exc
    return frozenset(asset_ids)


def _realm_roles(claims: Mapping[str, object]) -> frozenset[CaseRole]:
    realm_access = claims.get("realm_access")
    if not isinstance(realm_access, Mapping):
        raise AuthenticationError()
    raw_roles = realm_access.get("roles")
    if not isinstance(raw_roles, Sequence) or isinstance(raw_roles, str | bytes):
        raise AuthenticationError()

    roles: set[CaseRole] = set()
    for raw_role in raw_roles:
        if not isinstance(raw_role, str):
            raise AuthenticationError()
        try:
            roles.add(CaseRole(raw_role))
        except ValueError:
            # Keycloak may include its own non-product realm roles. They confer
            # no NetOps permission and are intentionally ignored.
            continue
    return frozenset(roles)
