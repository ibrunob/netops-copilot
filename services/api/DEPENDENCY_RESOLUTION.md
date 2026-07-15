# API dependency resolution

The project uses Python 3.12 and exact direct dependency pins in the root
[`pyproject.toml`](../../pyproject.toml). `uv.lock` records the resolved
dependency graph; the local PEP 517 build backend is exact-pinned as well.

## Lockfile status

`uv.lock` is committed. CI, local quality checks, and the API image use frozen
resolution, so a dependency change must update the lock deliberately.

Update it only from the supported Python 3.12 environment:

```sh
uv lock
uv sync --frozen --extra dev
```

The Docker build exports production dependencies from `uv.lock` and installs
them with hash verification before installing the local application wheel. Do
not use unconstrained `pip install` as a substitute for this workflow.

## Non-secret endpoint configuration

The API intentionally has only dependency *locations*, not credentials. Set nested
Pydantic environment variables only when Compose/Docker networking differs from the
defaults:

```sh
NETOPS_DEPENDENCIES__POSTGRES__HOST=postgres
NETOPS_DEPENDENCIES__POSTGRES__PORT=5432
NETOPS_DEPENDENCIES__REDIS__HOST=redis
NETOPS_DEPENDENCIES__REDIS__PORT=6379
NETOPS_DEPENDENCIES__MINIO__HOST=minio
NETOPS_DEPENDENCIES__MINIO__PORT=9000
NETOPS_DEPENDENCIES__TEMPORAL__HOST=temporal
NETOPS_DEPENDENCIES__TEMPORAL__PORT=7233
NETOPS_DEPENDENCIES__KEYCLOAK__HOST=keycloak
NETOPS_DEPENDENCIES__KEYCLOAK__PORT=8080
```

These values are deliberately not used by `/readyz` yet. There is no adapter or
authenticated dependency check to perform, so claiming those services are ready
would be misleading.
