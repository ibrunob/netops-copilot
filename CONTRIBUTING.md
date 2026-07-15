# Contributing to NetOps Copilot

NetOps Copilot is an evidence-first operational system. Changes must preserve
tenant isolation, immutable audit history, and the boundary between deterministic
validators and advisory AI output.

## Local prerequisites

- Docker Compose v2
- Python 3.12 (the version in `.python-version`)
- `uv`
- Node.js version from `.nvmrc` and npm 11 or newer

Start with a local environment file:

```sh
make env
```

The generated `.env` is untracked and contains unique local development
passwords. Never commit it, credentials, customer configuration, support
tickets, audio, or device artifacts. The tracked `.env.example` contains all
current non-secret names and describes the local-only secret variables.

## Reproducible bootstrap

`make bootstrap` intentionally installs only from committed locks:

```sh
make bootstrap
```

The committed `uv.lock` and `apps/web/package-lock.json` are required. There is
no fallback to opportunistic resolution. Use `uv sync --frozen --extra dev` and
`npm --prefix apps/web ci` exactly as the Makefile and CI do.

## Frontend verification

The web gate is deliberately the same locally and in CI: a locked install,
source checks, then an optimized production build.

```sh
npm --prefix apps/web ci
npm --prefix apps/web run check
NEXT_TELEMETRY_DISABLED=1 npm --prefix apps/web run build
```

Do not use `npm install` to repair a lock mismatch. Make intentional dependency
updates in `apps/web/package.json`, regenerate `apps/web/package-lock.json`, and
review both files together.

## Daily commands

```sh
make compose-config  # render every local Compose profile
make up              # core platform
make up-events       # core platform plus Redpanda
make lint
make typecheck
make test
make down            # preserves named development volumes
```

`make down` does not remove volumes. Do not use destructive Docker commands
against a shared environment.

## Change standards

- Keep product code in `apps/`, `services/`, or `packages/`.
- Add or update tests with every behavior change. Deterministic parser and
  validator changes require golden fixtures with exact evidence locations.
- Preserve organization scoping at the API and database layers. Frontend checks
  are never authorization controls.
- Use idempotency keys and optimistic versions for writes; append audit/event
  history in the same transaction as materialized state changes.
- Never place raw configuration, keys, passwords, tokens, private addresses,
  transcripts, or customer identifiers in logs, traces, test snapshots, or
  prompts. Use synthetic fixtures and redacted derivatives.
- Update architecture, API contract, runbook, or migration documentation when a
  change affects an operational boundary.

Install hooks after bootstrap:

```sh
uv run --frozen pre-commit install
uv run --frozen pre-commit run --all-files
```

The Python hooks intentionally use `uv run --frozen`; a missing or stale lock is
an error that must be fixed before merging. The web hook runs the same
`npm --prefix apps/web run check` command as CI and assumes the locked web
dependencies have already been installed. The `pre-commit` runner is installed
from the frozen Python development toolchain; hook revisions remain pinned in
`.pre-commit-config.yaml`.

## Pull requests

Keep pull requests focused and state the security, data-classification, and
operational impact. Include commands run and their output status. Schema,
authorization, artifact handling, workflow, integration, and AI changes need a
threat-model note and a rollback path.
