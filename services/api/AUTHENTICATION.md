# API authentication contract

The API is an OIDC resource server. It accepts only signed bearer **access
tokens** for the `netops-api` audience; browser ID tokens, opaque tokens, and
client-supplied organization headers are not authorization inputs.

`GET /v1/auth/me` is a protected diagnostic endpoint for the web session
boundary. Health endpoints remain intentionally unauthenticated so platform
orchestrators can probe them without a human session.

## Required verified claims

The verifier requires a valid asymmetric JWT signature, `exp`, `iat`, `sub`,
`iss`, and `aud`. The `iss` must equal `NETOPS_AUTH__ISSUER`; `aud` must contain
`NETOPS_AUTH__AUDIENCE` (default: `netops-api`). Signing keys are retrieved from
`NETOPS_AUTH__JWKS_URL`, which may be an internal URL distinct from the public
issuer. The local defaults are designed for a browser obtaining a token from
`http://localhost:8080` while the Compose API retrieves JWKS through the
`keycloak` service DNS name.

After signature verification, the product reads these Keycloak access-token
claims:

| Claim | Type | Meaning |
| --- | --- | --- |
| `sub` | non-empty string | Stable OIDC subject; never supplied by the client as an actor ID. |
| `realm_access.roles` | string array | Product roles: `org_admin`, `operator`, `approver`, `auditor`, `integration_admin`, `platform_admin`. Unknown Keycloak roles grant no NetOps permission. |
| `organization_id` | UUID string | Required tenant boundary. Future repository calls set their database tenant context from this claim only. |
| `asset_ids` | optional array of UUID strings | Explicit asset scope. An absent or empty value grants access to **no** assets, not all assets. |
| `azp` | optional non-empty string | Authorized OIDC client, retained as audited identity metadata. |

Roles shape which actions are permitted, but they do not override organization
or asset scope. `platform_admin` can satisfy API role checks; it still receives
an organization and explicit asset scope for organization-owned resource
operations. PostgreSQL RLS will remain the independent second enforcement layer
when M1 persistence is added.

## Local Keycloak setup

The development realm import defines the public PKCE web client `netops-web`,
the resource audience client `netops-api`, all product realm roles, and token
mappers for the claims above. It intentionally contains no users or passwords.

Create a temporary user through the local Keycloak admin console, assign one or
more product realm roles, and set these user attributes:

```text
organization_id = <organization UUID>
asset_ids       = <asset UUID>       # repeat the attribute for each permitted asset
```

Obtain an authorization-code-with-PKCE access token for `netops-web`, then call:

```sh
curl --fail-with-body http://127.0.0.1:8000/v1/auth/me \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

For the complete local signed-trace acceptance check, `make verify-pkce-trace`
starts a loopback-only PKCE callback at `127.0.0.1:8765`, prints the Keycloak
authorization URL, and exchanges the returned code only in memory before
calling `/v1/auth/me` and checking Tempo. It never asks for, stores, or prints
the user's password or access token. The imported realm permits only that exact
loopback callback URI.

If the development realm was imported before this change, Keycloak will not
re-import it into the existing database. Recreate the local realm or apply the
same audience and user-attribute mappers in the admin console; never erase a
shared Keycloak database to refresh a development import.

## Runtime dependency

The API image must install the pinned runtime dependency
`PyJWT[crypto]==2.10.1`. `crypto` supplies the asymmetric algorithms needed to
verify Keycloak's RS256 signing keys. Protected requests fail closed if JWT
verification support or the IdP's trusted JWKS is unavailable; there is no
development bypass.
