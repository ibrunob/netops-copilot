# Local infrastructure

`docker-compose.yml` runs the current local application boundary alongside its
development dependencies: the FastAPI domain API and the Next.js web product
shell. The future worker and connector agent are not part of this Compose stack
yet.

## Start and stop

```sh
docker compose --profile core up -d
docker compose --profile core --profile events up -d
docker compose --profile observability up -d
docker compose --profile test up -d postgres-test
docker compose --profile core down
```

The `core` profile starts PostgreSQL 16 with pgvector, Redis, MinIO, Temporal
and Temporal UI, a development-only Keycloak realm, the API, and the web app.
The API is available at `http://127.0.0.1:8000` and the web app at
`http://127.0.0.1:3000`. The optional `events` profile starts Redpanda for
testing the transactional-outbox consumer path. Profiles are deliberately
explicit so that a command such as `docker compose up` cannot accidentally
start a partial platform. The `observability` profile starts an OpenTelemetry
Collector, Tempo, Prometheus, and Grafana. The `test` profile starts only an
isolated PostgreSQL cluster for migration and integration tests. Image tags are
pinned to release versions; production image promotion must additionally pin
digests.

Use the Make targets for the normal developer paths:

```sh
make up
make up-events
make up-observability
make test-db-up
```

`make up-observability` first starts the complete core profile, brings up the
local observability services, then recreates only the API with its private OTLP
HTTP endpoint. Normal `make up` leaves trace export disabled, so an absent
collector cannot cause local exporter retry noise.

The worker is not included yet: the directory intentionally has no executable
worker process. It must join the `core` profile only when the Temporal worker
and outbox publisher are real, observable processes; a placeholder container
would conceal that missing implementation.

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
| OTel collector (observability) | `127.0.0.1:4317` / `127.0.0.1:4318` | OTLP gRPC / HTTP ingest |
| Prometheus (observability) | `http://127.0.0.1:9090` | Local metrics query UI |
| Tempo (observability) | `http://127.0.0.1:3200` | Local trace store API |
| Grafana (observability) | `http://127.0.0.1:3001` | Anonymous local dashboards only |
| Test PostgreSQL (test profile) | `127.0.0.1:5433` | Isolated migrations/integration database |

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
`pg_stat_statements`, and `pg_trgm` only in the application database.
Application migrations belong to Alembic. The core Compose profile runs its
one-shot `migrate` service before the API starts, using the PostgreSQL bootstrap
owner only for schema work; the API then uses the separate `netops_app` role.
The test profile has its own volume and initializes only its `netops_test`
application database with the same extensions; it never touches the core
PostgreSQL volume.

## Migrations and isolated test database

Alembic receives its database URL only through the `NETOPS_DATABASE_URL`
process environment variable. The tracked `services/api/alembic.ini` therefore
contains a placeholder URL and is safe to commit. The Make targets source the
ignored root `.env` only long enough to construct a local URL:

```sh
make migrate
make test-migrate
make test-rls
```

`make migrate` targets the core application database. `make test-migrate`
starts and waits for the dedicated `postgres-test` container at port `5433`,
then targets its independent `netops_test` database. It intentionally does not
run as part of `make test`: pure unit tests must not acquire infrastructure
implicitly. Test suites that need PostgreSQL should use `make test-migrate`
first, isolate their data with transactions/schema fixtures, and never point a
test URL at the core port `5432`.

The first revision creates tenant identity, asset, settings, and audit tables;
it uses fail-closed PostgreSQL RLS and an unprivileged `netops_app` role. Run
`make test-rls` to exercise direct SQL, the runtime tenant-transaction boundary,
cross-tenant reads/writes, malformed or missing context, connection reuse, and
`FORCE ROW LEVEL SECURITY` against the isolated test database. It is an
acceptance gate, not a mocked unit test.

To dispose of only test data, run the guarded command below. It does not affect
the core database or any other named volume.

```sh
CONFIRM_TEST_DB_RESET=1 make test-db-reset
```

## Local PostgreSQL backup and restore drill

The local backup target exports only the application database in PostgreSQL's
custom archive format. It excludes ownership and database-role definitions (so
it contains no role passwords), but retains table grants required by the
unprivileged `netops_app` runtime role. Archives are written below the ignored
`tmp/` directory by default and do not back up MinIO artifacts, Temporal
history, Keycloak, or Docker volumes.

```sh
make db-backup
make db-backup BACKUP_FILE=tmp/backups/before-migration.dump
```

To perform a restore drill, first stop callers that may write through the API.
The restore target terminates remaining application-database sessions, drops
objects present in the archive, reloads the selected local database, then runs
Alembic to bring an older archive forward to the current migration head. It
cannot run without an explicit acknowledgement:

```sh
CONFIRM_LOCAL_RESTORE=1 make db-restore BACKUP_FILE=tmp/backups/before-migration.dump
```

For an automated proof that never addresses the core database, run:

```sh
make db-restore-drill
```

This target uses only the `postgres-test` container and refuses to run when its
configured database name matches the core database name. It records the
migrated revision, inserts a temporary application sentinel row, takes an
archive, removes the row, restores the archive, and verifies both the same
Alembic revision and sentinel are back. It then runs `make test-rls`, proving
the restored grants, runtime `netops_app` role, and tenant RLS policies still
work. The transient archive is deleted even on failure; the sentinel is removed
before the RLS suite starts. This is a local developer drill, not a production
backup design:
production requires managed PostgreSQL PITR, separately versioned object-store
backups, encryption controls, access audit, and regularly observed restores.

## Readiness strategy

PostgreSQL, Redis, MinIO, Temporal, Temporal UI, Keycloak, Redpanda, Prometheus,
Grafana, the API, and the web application each declare health checks. Dependents wait for the
upstream health check instead of relying on container start order: Temporal and
Keycloak wait for PostgreSQL, Temporal UI waits for Temporal, and the one-shot
MinIO bootstrap waits for MinIO. The one-shot migration service waits for
PostgreSQL, and the API waits for its successful completion plus PostgreSQL,
Redis, MinIO, bucket bootstrap, Temporal, and Keycloak; the web container then
waits for the API process health check.

The API `/healthz` check confirms only that the ASGI process is serving. `/readyz`
also opens an application-role database connection and executes `SELECT 1` in a
transaction with a one-second local statement timeout. It returns a sanitized
`503 persistence_unavailable` response when PostgreSQL is unavailable and never
creates a tenant context for an unauthenticated platform probe. Compose still
gates the API on its upstream service health checks. `make verify-local` asserts
that this database probe succeeds. The web app receives the server-only
`NETOPS_API_BASE_URL=http://api:8000`; no `NEXT_PUBLIC_*` API URL or credential
is exposed to the browser.

## Signed trace verification

After `make up-observability`, create a temporary Keycloak user and obtain an
authorization-code-with-PKCE access token as described in
[`services/api/AUTHENTICATION.md`](../services/api/AUTHENTICATION.md). Keep that
short-lived token only in your current shell, then run:

```sh
NETOPS_ACCESS_TOKEN="$ACCESS_TOKEN" make verify-signed-trace
```

The verifier calls the signed `/v1/auth/me` endpoint with a fresh W3C trace ID,
checks that the API preserves it, and waits for that exact trace to appear in
local Tempo. It never prints or saves the access token.

## Configuration for root `.env.example`

The root configuration owner should document these non-secret endpoint and
port variables: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PORT`,
`POSTGRES_TEST_DB`, `POSTGRES_TEST_USER`, `POSTGRES_TEST_PORT`,
`MINIO_API_PORT`, `MINIO_CONSOLE_PORT`, `MINIO_BUCKET`, `TEMPORAL_GRPC_PORT`,
`TEMPORAL_UI_PORT`, `KEYCLOAK_PORT`, `REDPANDA_KAFKA_PORT`,
`REDPANDA_ADMIN_PORT`, `REDPANDA_SCHEMA_REGISTRY_PORT`, `OTEL_GRPC_PORT`,
`OTEL_HTTP_PORT`, `OTEL_METRICS_PORT`, `PROMETHEUS_PORT`, `TEMPO_HTTP_PORT`,
and `GRAFANA_PORT`.

It must also document the five required secret variable names from
**Required local secrets**, without assigning example values. Do not add real
credentials or password-shaped defaults to any tracked configuration file.
