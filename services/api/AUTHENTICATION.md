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

For a real browser exercise, create a local user with the `operator` realm
role, choose one fresh local UUID for `organization_id`, and copy the user's
Keycloak **ID** (not the username) into `KC_SUBJECT`. Then create the matching
local identity records. This command is intentionally restricted to the local
Compose database; it neither creates an IdP user nor prints a password or
token:

```sh
ORG_ID=11111111-1111-4111-8111-111111111111 \
KC_SUBJECT='<copy the Keycloak user ID>' \
docker compose --env-file .env --profile core exec -T -e ORG_ID -e KC_SUBJECT postgres \
  sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 --set=organization_id="$ORG_ID" --set=subject="$KC_SUBJECT"' <<'SQL'
INSERT INTO organizations (id, slug, display_name)
  VALUES (:'organization_id'::uuid, 'local-operator', 'Local Operator')
  ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name;
INSERT INTO users (oidc_subject, display_name)
  VALUES (:'subject', 'local-operator')
  ON CONFLICT (oidc_subject) DO UPDATE SET display_name = EXCLUDED.display_name;
INSERT INTO memberships (organization_id, user_id, role)
  SELECT :'organization_id'::uuid, id, 'operator' FROM users WHERE oidc_subject = :'subject'
  ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role;
SQL
```

Set that exact `ORG_ID` as the Keycloak user's `organization_id` attribute
before signing in. The signed token remains the authorization source; the local
`users` and `memberships` rows make the persistence model match the temporary
principal and are not a browser-controlled bypass. The example UUID and slug
are development fixtures only—do not run the command against a shared,
staging, or production database.

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
