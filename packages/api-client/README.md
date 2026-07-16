# Generated API client boundary

`openapi.json` and `src/generated.ts` are checked artifacts generated from the
FastAPI application, not hand-maintained parallel contracts.

Regenerate after changing an API route or model:

```sh
uv run --frozen python scripts/generate-api-client.py
```

Validate that the checked client still matches the API contract:

```sh
uv run --frozen python scripts/generate-api-client.py --check
```

The generated client is a narrow browser/server boundary: callers provide the
base URL and an access-token provider; organization, actor, and asset scope are
always derived by the API from the signed token.
