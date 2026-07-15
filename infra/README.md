# Local infrastructure

`docker-compose.yml` runs the current local application boundary alongside its
development dependencies: the FastAPI domain API and the Next.js web product
shell. The future worker and connector agent are not part of this Compose stack
yet.

## Start and stop

```sh
docker compose --profile core up -d
docker compose --profile core --profile events up -d
docker compose --profile core down
```

The `core` profile starts PostgreSQL 16 with pgvector, Redis, MinIO, Temporal
and Temporal UI, a development-only Keycloak realm, the API, and the web app.
The API is available at `http://127.0.0.1:8000` and the web app at
`http://127.0.0.1:3000`. The optional `events` profile starts Redpanda for
testing the transactional-outbox consumer path. Profiles are deliberately
explicit so that a command such as `docker compose up` cannot accidentally
start a partial platform. Image tags are pinned to release versions; production
image promotion must additionally pin digests.

## Required local secrets

Before starting Compose, create an ignored root `.env` file with fresh,
developer-local values for the following variables. Compose intentionally fails
fast if any are omitted; it no longer contains fallback passwords or human
operator credentials.

```sh
POSTGRES_PASSWORD
MINIO_ROOT_USER
MINIO_ROOT_PASSWORD
KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME
KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD
```

Assign each name a fresh value in the ignored file. Do not commit the file,
reuse a value outside this machine, or add a Keycloak user/password to the
realm import. The imported realm provides roles and the PKCE-enabled web client
only. Create temporary test users through the local Keycloak admin UI after
startup, then remove them when no longer needed.

## Local endpoints

| Service | Host endpoint | Purpose |
| --- | --- | --- |
| Web | `http://127.0.0.1:3000` | Next.js product shell |
| API | `http://127.0.0.1:8000` | FastAPI domain API |
| PostgreSQL | `127.0.0.1:5432` | Application and local service databases |
| Redis | internal only | Ephemeral cache and rate limiting |
| MinIO API / console | `127.0.0.1:9000` / `127.0.0.1:9001` | S3-compatible artifacts |
| Temporal / UI | `127.0.0.1:7233` / `127.0.0.1:8233` | Durable workflows and inspection |
| Keycloak | `http://127.0.0.1:8080` | Local OIDC issuer; realm `netops-dev` |
| Redpanda (events profile) | `127.0.0.1:19092` | Kafka-compatible local event broker |

The listed ports are bound only to loopback and may be overridden in the root
`.env` file. Redis has no host port and is reachable only by Compose services.
Production uses a secret manager and managed service credentials; this Compose
file is intentionally limited to local development.

## Persistence and reset

Named volumes use an explicit `netops-copilot-*` convention, and therefore
survive `docker compose down`. This makes local workflow history, object
artifacts, and database data durable across container restarts. To intentionally
discard a local environment, stop the stack and remove only the named volumes
you mean to reset. Never use that procedure for a shared environment.

PostgreSQL initialization creates the `temporal`, `temporal_visibility`, and
`keycloak` service databases. It enables `vector`, `pgcrypto`, and
`pg_stat_statements` only in the application database. Application migrations
belong to Alembic and are not run by Compose.

## Readiness strategy

PostgreSQL, Redis, MinIO, Temporal, Temporal UI, Keycloak, Redpanda, the API,
and the web application each declare health checks. Dependents wait for the
upstream health check instead of relying on container start order: Temporal and
Keycloak wait for PostgreSQL, Temporal UI waits for Temporal, and the one-shot
MinIO bootstrap waits for MinIO. The API waits for PostgreSQL, Redis, MinIO,
bucket bootstrap, Temporal, and Keycloak before it starts; the web container
then waits for the API process health check.

The current API `/healthz` check confirms only that the ASGI process is serving.
It is deliberately **not** described as a database or dependency readiness
check: Compose gates the API on the actual dependency service health checks.
When application adapters add real dependency probes, those belong in the API
readiness endpoint and must be documented separately. The web app receives the
server-only `NETOPS_API_BASE_URL=http://api:8000`; no `NEXT_PUBLIC_*` API URL or
credential is exposed to the browser.

## Configuration for root `.env.example`

The root configuration owner should document these non-secret endpoint and
port variables: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PORT`,
`MINIO_API_PORT`, `MINIO_CONSOLE_PORT`, `MINIO_BUCKET`, `TEMPORAL_GRPC_PORT`,
`TEMPORAL_UI_PORT`, `KEYCLOAK_PORT`, `REDPANDA_KAFKA_PORT`,
`REDPANDA_ADMIN_PORT`, and `REDPANDA_SCHEMA_REGISTRY_PORT`.

It must also document the five required secret variable names from
**Required local secrets**, without assigning example values. Do not add real
credentials or password-shaped defaults to any tracked configuration file.
