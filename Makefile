SHELL := /bin/sh

UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose
WEB_DIR := apps/web
ENV_FILE := .env

.DEFAULT_GOAL := help

.PHONY: help env check-tools check-locks bootstrap up up-events down compose-config verify-local secret-hygiene lint typecheck test migrate seed

help: ## Show the supported local development commands.

	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create an untracked local environment file from the safe template.

	@if [ ! -f "$(ENV_FILE)" ]; then \
		cp .env.example "$(ENV_FILE)"; \
		chmod 600 "$(ENV_FILE)"; \
	fi
	@command -v openssl >/dev/null || { printf '%s\n' "openssl is required to generate local development secrets." >&2; exit 1; }
	@for key in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD; do \
		if ! grep -q "^$$key=.\+" "$(ENV_FILE)"; then \
			printf '%s=%s\n' "$$key" "$$(openssl rand -hex 24)" >> "$(ENV_FILE)"; \
		fi; \
	done
	@if ! grep -q '^KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME=.\+' "$(ENV_FILE)"; then \
		printf '%s\n' "KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME=netops-admin" >> "$(ENV_FILE)"; \
	fi
	@chmod 600 "$(ENV_FILE)"; \
	printf '%s\n' "Verified $(ENV_FILE) contains generated local-only secrets. Never commit it."

check-tools: ## Verify that the pinned development tool families are present.

	@command -v "$(UV)" >/dev/null || { printf '%s\n' "uv is required; install it before running Python commands." >&2; exit 1; }
	@command -v "$(NPM)" >/dev/null || { printf '%s\n' "npm is required; use the Node version in .nvmrc." >&2; exit 1; }
	@command -v docker >/dev/null || { printf '%s\n' "Docker Compose is required; install Docker Desktop or a compatible Docker engine." >&2; exit 1; }

check-locks: ## Fail explicitly until reproducibility locks have been committed.

	@test -f uv.lock || { printf '%s\n' "Missing uv.lock. Generate it with Python 3.12 using 'uv lock' and commit it; see services/api/DEPENDENCY_RESOLUTION.md." >&2; exit 1; }
	@test -f $(WEB_DIR)/package-lock.json || { printf '%s\n' "Missing $(WEB_DIR)/package-lock.json. Generate it with npm from the pinned package manifest and commit it before using npm ci." >&2; exit 1; }

bootstrap: env check-tools check-locks ## Install locked Python and web dependencies.

	$(UV) sync --frozen --extra dev
	$(NPM) --prefix $(WEB_DIR) ci

up: env ## Start the core local platform (PostgreSQL, Redis, MinIO, Temporal, Keycloak).

	$(COMPOSE) --env-file $(ENV_FILE) --profile core up -d --build

up-events: env ## Start the core platform plus Redpanda.

	$(COMPOSE) --env-file $(ENV_FILE) --profile core --profile events up -d --build

down: env ## Stop local platform containers without deleting named volumes.

	$(COMPOSE) --env-file $(ENV_FILE) --profile core --profile events down

compose-config: env ## Render and validate the complete local Compose configuration.

	$(COMPOSE) --env-file $(ENV_FILE) --profile core --profile events config --quiet

verify-local: env check-locks ## Verify the live core API and web services without exposing secrets.

	sh scripts/verify-local-stack.sh

secret-hygiene: ## Scan source files for tracked dotenv files and likely literal credentials.

	sh scripts/check-secret-hygiene.sh

lint: check-tools check-locks ## Run Python and web lint checks from locked environments.

	$(UV) run --frozen ruff check services/api
	$(NPM) --prefix $(WEB_DIR) run lint

typecheck: check-tools check-locks ## Run Python and TypeScript type checks from locked environments.

	$(UV) run --frozen mypy
	$(NPM) --prefix $(WEB_DIR) run typecheck

test: check-tools check-locks ## Run the API and web test suites from locked environments.

	$(UV) run --frozen pytest
	$(NPM) --prefix $(WEB_DIR) run test

migrate: ## Fail until the M1 Alembic migration package is implemented.

	@printf '%s\n' "Migrations are not available: Milestone 1 persistence/Alembic work has not been implemented." >&2
	@exit 1

seed: ## Fail until M1/M2 provide an explicit, tenant-safe development seed path.

	@printf '%s\n' "Seeding is not available: no tenant-safe persistence seed contract exists yet." >&2
	@exit 1
