from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from netops_api.core import auth
from netops_api.core.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthorizationError,
    JwtTokenVerifier,
    principal_from_claims,
)
from netops_api.core.config import AuthSettings
from netops_api.main import get_application_dependencies

ORGANIZATION_ID = UUID("11111111-1111-1111-1111-111111111111")
ASSET_ID = UUID("22222222-2222-2222-2222-222222222222")
SECOND_ORGANIZATION_ID = UUID("33333333-3333-3333-3333-333333333333")


class StubTokenVerifier:
    def __init__(self, principal: AuthenticatedPrincipal | Exception) -> None:
        self.principal = principal

    async def verify(self, _: str) -> AuthenticatedPrincipal:
        if isinstance(self.principal, Exception):
            raise self.principal
        return self.principal


class StubKeyResolver:
    async def get_signing_key(self, key_id: str, _: Any) -> str:
        assert key_id == "signing-key-1"
        return "public-key"


class FixedKeyResolver:
    def __init__(self, signing_key: object) -> None:
        self.signing_key = signing_key

    async def get_signing_key(self, key_id: str, _: Any) -> object:
        assert key_id == "signing-key-1"
        return self.signing_key


class FakeInvalidTokenError(Exception):
    pass


class FakeJwt:
    InvalidTokenError = FakeInvalidTokenError

    def __init__(self, claims: dict[str, object] | Exception) -> None:
        self.claims = claims
        self.decode_arguments: dict[str, object] | None = None

    def get_unverified_header(self, _: str) -> dict[str, str]:
        return {"alg": "RS256", "kid": "signing-key-1"}

    def decode(self, _: str, key: str, **kwargs: object) -> dict[str, object]:
        assert key == "public-key"
        self.decode_arguments = kwargs
        if isinstance(self.claims, Exception):
            raise self.claims
        return self.claims


def _claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "sub": "keycloak-subject-1",
        "iss": "http://localhost:8080/realms/netops-dev",
        "aud": "netops-api",
        "exp": 4_000_000_000,
        "iat": 1_700_000_000,
        "azp": "netops-web",
        "organization_id": str(ORGANIZATION_ID),
        "asset_ids": [str(ASSET_ID)],
        "realm_access": {"roles": ["operator", "offline_access"]},
    }
    claims.update(overrides)
    return claims


def _principal() -> AuthenticatedPrincipal:
    return principal_from_claims(_claims(), AuthSettings())


def _replace_token_verifier(
    app: FastAPI, token_verifier: StubTokenVerifier
) -> None:
    app.state.dependencies = replace(
        get_application_dependencies(app), token_verifier=token_verifier
    )


def test_principal_is_built_from_signed_claims_with_no_implicit_asset_grant() -> None:
    principal = principal_from_claims(_claims(asset_ids=None), AuthSettings())

    assert principal.subject == "keycloak-subject-1"
    assert principal.organization_id == ORGANIZATION_ID
    assert principal.roles == frozenset({"operator"})
    assert principal.asset_ids == frozenset()

    with pytest.raises(AuthorizationError, match="asset_scope_denied"):
        principal.require_asset(ASSET_ID)


@pytest.mark.parametrize(
    ("claim_name", "value"),
    [
        ("organization_id", "not-a-uuid"),
        ("organization_id", None),
        ("asset_ids", str(ASSET_ID)),
        ("asset_ids", ["not-a-uuid"]),
        ("realm_access", {"roles": "operator"}),
    ],
)
def test_malformed_scope_or_role_claim_is_rejected(claim_name: str, value: object) -> None:
    with pytest.raises(AuthenticationError):
        principal_from_claims(_claims(**{claim_name: value}), AuthSettings())


def test_verified_principal_enforces_role_organization_and_asset_scope() -> None:
    principal = _principal()

    principal.require_any_role("operator")
    principal.require_organization(ORGANIZATION_ID)
    principal.require_asset(ASSET_ID)

    with pytest.raises(AuthorizationError, match="insufficient_role"):
        principal.require_any_role("approver")
    with pytest.raises(AuthorizationError, match="organization_scope_denied"):
        principal.require_organization(SECOND_ORGANIZATION_ID)


@pytest.mark.anyio
async def test_jwt_verifier_requires_expected_issuer_audience_and_registered_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_jwt = FakeJwt(_claims())
    monkeypatch.setattr(auth, "_load_pyjwt", lambda: fake_jwt)
    verifier = JwtTokenVerifier(AuthSettings(), StubKeyResolver())

    principal = await verifier.verify("header.payload.signature")

    assert principal.organization_id == ORGANIZATION_ID
    assert fake_jwt.decode_arguments == {
        "algorithms": ["RS256"],
        "audience": "netops-api",
        "issuer": "http://localhost:8080/realms/netops-dev",
        "options": {"require": ["exp", "iat", "sub", "iss", "aud"]},
        "leeway": 30,
    }


@pytest.mark.anyio
async def test_jwt_verifier_maps_signature_failure_to_safe_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_jwt = FakeJwt(FakeInvalidTokenError("signature mismatch"))
    monkeypatch.setattr(auth, "_load_pyjwt", lambda: fake_jwt)
    verifier = JwtTokenVerifier(AuthSettings(), StubKeyResolver())

    with pytest.raises(AuthenticationError, match="invalid_token"):
        await verifier.verify("header.payload.signature")


@pytest.mark.anyio
async def test_jwt_verifier_accepts_a_real_keycloak_style_rsa_access_token() -> None:
    jwt = pytest.importorskip("jwt")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        _claims(),
        private_key,
        algorithm="RS256",
        headers={"kid": "signing-key-1"},
    )
    verifier = JwtTokenVerifier(AuthSettings(), FixedKeyResolver(private_key.public_key()))

    principal = await verifier.verify(token)

    assert principal.subject == "keycloak-subject-1"


def test_identity_endpoint_requires_a_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"


def test_identity_endpoint_returns_only_verified_identity_claims(
    app: FastAPI, client: TestClient
) -> None:
    _replace_token_verifier(app, StubTokenVerifier(_principal()))

    response = client.get("/v1/auth/me", headers={"Authorization": "Bearer access-token"})

    assert response.status_code == 200
    assert response.json() == {
        "subject": "keycloak-subject-1",
        "organization_id": str(ORGANIZATION_ID),
        "roles": ["operator"],
        "asset_ids": [str(ASSET_ID)],
    }


def test_identity_endpoint_hides_invalid_token_details(
    app: FastAPI, client: TestClient
) -> None:
    _replace_token_verifier(app, StubTokenVerifier(AuthenticationError()))

    response = client.get("/v1/auth/me", headers={"Authorization": "Bearer access-token"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    assert response.json()["error"] == {
        "code": "invalid_token",
        "message": "The access token is invalid or expired.",
        "request_id": response.headers["X-Correlation-ID"],
        "details": None,
    }
