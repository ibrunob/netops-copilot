# NetOps Copilot

NetOps Copilot is a production-oriented, evidence-first network operations system: durable case handling, deterministic config validation, human-approved remediation, and persistent institutional memory.

## Product architecture

The target consists of a Next.js web application, FastAPI domain API, PostgreSQL with pgvector, encrypted object storage, Temporal workflows, an event outbox, isolated deterministic validator workers, and an evidence-bound OpenAI gateway. The system begins read-only: it drafts and validates config diffs but cannot push device configuration.

- [Production architecture](docs/ARCHITECTURE.md)
- [Dependency-ordered implementation TODO](docs/IMPLEMENTATION_TODO.md)

## Run the local foundation

Prerequisites: Docker Desktop, Node from `.nvmrc`, and `uv` with Python 3.12.8.

```sh
make bootstrap
make up
make verify-local
```

`make env` creates an ignored `.env` with local-only secrets. If local API or
PostgreSQL ports are occupied, adjust `NETOPS_PORT` or `POSTGRES_PORT` in that
ignored file. `make down` stops containers without removing named data volumes.
Set `COMPOSE_PROJECT_NAME` when running an isolated stack; its data volumes are
namespaced with that project name and cannot reuse another local stack's data.

Run `make secret-hygiene` before committing configuration changes. It works
offline, checks tracked plus non-ignored source files for dotenv files and likely
literal credentials, and reports only paths and rule categories—not values.

The web quality gate always installs from the committed lock before it runs:

```sh
npm --prefix apps/web ci
npm --prefix apps/web run check
NEXT_TELEMETRY_DISABLED=1 npm --prefix apps/web run build
```

CI executes the same commands, including the optimized production build.

The current implementation includes a Next.js product shell, a FastAPI OIDC resource-server boundary, local platform profiles, immutable case-transition engine, Cisco IOS Phase 2 lifetime validator, and line-preserving secret redaction. Persistence with tenant RLS, authenticated web UX, triage workflows, retrieval, and AI analysis remain in their dependency-ordered build stages.

The repository now also has the target worker, connector-agent, generated-client,
cross-service test, and ADR boundaries. They contain no fake runtime behavior:
their README files describe the implementation gate for each package. M1 now
includes locked Alembic/SQLAlchemy tooling, an isolated test database, local
backup/restore commands, and migration scaffolding. `make seed` continues to
fail until M1/M2 define an explicit tenant-safe persistence seed contract.
