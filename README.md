# NetOps Copilot

NetOps Copilot is a production-oriented, evidence-first network operations system: durable case handling, deterministic config validation, human-approved remediation, and persistent institutional memory.

> **OpenAI Build Week submission — Developer Tools.** NetOps Copilot turns a
> network-operations ticket into an auditable case record. It keeps the
> evidence, identity, state changes, and approvals together, rather than
> allowing an assistant to make an unreviewed configuration change.

## What judges can run today

- Sign in through a real local Keycloak OIDC flow; browser code never receives
  the API bearer token.
- Triage real PostgreSQL-backed cases with immutable event history,
  idempotency, optimistic concurrency, tenant RLS, and live SSE updates.
- Create a new incident, move it through an operator-controlled workflow, and
  leave a case pending for a customer response.
- Paste or upload a configuration for a redacted preview, and attach audio
  evidence through signed MinIO uploads, ClamAV scanning, and persisted
  artifact status.
- Explore a seeded demo queue with five realistic incidents. These are rows in
  the local database and use the same API and authorization path as a new
  case—not browser-local mock data.

The system intentionally does **not** push configuration to network devices.
Its evidence-bound AI diagnosis, Cisco parser, and autonomous remediation
stages remain explicitly out of scope for this demo; this is a deliberate
human-approval boundary, not a simulated capability.

## Fast demo

Prerequisites: Docker Desktop, Node from `.nvmrc`, and `uv` with Python
3.12.8.

```sh
make bootstrap
make demo
```

Open [http://localhost:3000](http://localhost:3000), sign in as
`demo-operator` / `netops-demo`, then open the case workspace. If you are using
the Codex in-app browser instead, use `http://0.0.0.0:3000` and set
`NETOPS_OIDC_PUBLIC_HOST=0.0.0.0` plus `NETOPS_COOKIE_SECURE=false` in the
ignored local `.env` before starting the stack.

For the 2–3 minute judge walkthrough, follow
[the demo runbook](docs/DEMO_RUNBOOK.md). Submission-ready Devpost copy, the
video script, and the fields that need the submitter's own links are in
[the submission pack](docs/DEVPOST_SUBMISSION.md).

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

The current implementation includes a Next.js product shell, FastAPI OIDC
resource-server boundary, local platform profiles, immutable case-transition
engine, secure artifact intake, line-preserving secret redaction, and a
generated TypeScript API client. The first tenant schema uses PostgreSQL RLS
and a separate runtime database role; run `make test-rls` to execute its real
isolated-database adversarial checks. The deterministic Cisco parser, durable
triage automation, retrieval, and evidence-bound AI diagnosis remain in their
dependency-ordered build stages.

The repository also has the target worker, connector-agent, generated client,
cross-service tests, and ADR boundaries. M1 includes locked Alembic/SQLAlchemy
tooling, an isolated test database, local backup/restore commands, and migration
scaffolding. `make seed` is an idempotent, tenant-safe local demo-data contract.
