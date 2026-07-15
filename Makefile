SHELL := /bin/sh

UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose
WEB_DIR := apps/web
ENV_FILE := .env
ALEMBIC_CONFIG := services/api/alembic.ini
BACKUP_FILE ?= tmp/backups/netops-local.dump

.DEFAULT_GOAL := help

.PHONY: help env check-tools check-locks bootstrap up up-events up-observability down compose-config verify-local secret-hygiene lint typecheck test migrate test-db-up test-db-ready test-db-down test-db-reset test-migrate test-rls db-backup db-restore seed

help: ## Show the supported local development commands.

	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create an untracked local environment file from the safe template.

	@if [ ! -f "$(ENV_FILE)" ]; then \
		cp .env.example "$(ENV_FILE)"; \
		chmod 600 "$(ENV_FILE)"; \
	fi
	@command -v openssl >/dev/null || { printf '%s\n' "openssl is required to generate local development secrets." >&2; exit 1; }
	@for key in POSTGRES_PASSWORD POSTGRES_APP_PASSWORD MINIO_ROOT_PASSWORD KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD; do \
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

up-observability: env ## Start the optional local OTel, Tempo, Prometheus, and Grafana stack.

	$(COMPOSE) --env-file $(ENV_FILE) --profile observability up -d

down: env ## Stop local platform containers without deleting named volumes.

	$(COMPOSE) --env-file $(ENV_FILE) --profile core --profile events down

compose-config: env ## Render and validate the complete local Compose configuration.

	$(COMPOSE) --env-file $(ENV_FILE) --profile core --profile events --profile test --profile observability config --quiet

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

migrate: env check-tools check-locks ## Apply Alembic migrations to the isolated local application database.

	@set -a; . "./$(ENV_FILE)"; set +a; \
		NETOPS_DATABASE_URL="postgresql+psycopg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@127.0.0.1:$${POSTGRES_PORT:-5432}/$$POSTGRES_DB"; \
		NETOPS_APPLICATION_DB_PASSWORD="$$POSTGRES_APP_PASSWORD"; \
		export NETOPS_DATABASE_URL NETOPS_APPLICATION_DB_PASSWORD; \
		$(UV) run --frozen alembic -c $(ALEMBIC_CONFIG) upgrade head

test-db-up: env ## Start the isolated PostgreSQL cluster used by migration and integration tests.

	$(COMPOSE) --env-file $(ENV_FILE) --profile test up -d postgres-test

test-db-ready: test-db-up ## Wait until the isolated test PostgreSQL cluster accepts connections.

	@set -a; . "./$(ENV_FILE)"; set +a; \
		attempt=1; \
		while [ "$$attempt" -le 30 ]; do \
			if $(COMPOSE) --env-file $(ENV_FILE) --profile test exec -T postgres-test pg_isready -U "$${POSTGRES_TEST_USER:-netops_test}" -d "$${POSTGRES_TEST_DB:-netops_test}" >/dev/null 2>&1; then \
				exit 0; \
			fi; \
			attempt=$$((attempt + 1)); \
			sleep 1; \
		done; \
		printf '%s\n' "postgres-test did not become ready within 30 seconds." >&2; \
		exit 1

test-db-down: env ## Stop and remove only the test database container; keep its named volume.

	@$(COMPOSE) --env-file $(ENV_FILE) --profile test stop postgres-test >/dev/null 2>&1 || true
	@$(COMPOSE) --env-file $(ENV_FILE) --profile test rm -f postgres-test >/dev/null 2>&1 || true

test-db-reset: env ## Destroy only the isolated test DB volume; requires CONFIRM_TEST_DB_RESET=1.

	@[ "$$CONFIRM_TEST_DB_RESET" = "1" ] || { printf '%s\n' "Refusing to reset test data. Re-run with CONFIRM_TEST_DB_RESET=1." >&2; exit 2; }
	@$(COMPOSE) --env-file $(ENV_FILE) --profile test rm -s -f postgres-test >/dev/null 2>&1 || true
	@set -a; . "./$(ENV_FILE)"; set +a; \
		volume="$${COMPOSE_PROJECT_NAME:-netops-copilot}-postgres-test-data"; \
		if docker volume inspect "$$volume" >/dev/null 2>&1; then docker volume rm "$$volume"; fi

test-migrate: test-db-ready check-tools check-locks ## Apply Alembic migrations to the isolated test database.

	@set -a; . "./$(ENV_FILE)"; set +a; \
		NETOPS_DATABASE_URL="postgresql+psycopg://$${POSTGRES_TEST_USER:-netops_test}:$$POSTGRES_PASSWORD@127.0.0.1:$${POSTGRES_TEST_PORT:-5433}/$${POSTGRES_TEST_DB:-netops_test}"; \
		NETOPS_APPLICATION_DB_PASSWORD="$$POSTGRES_APP_PASSWORD"; \
		export NETOPS_DATABASE_URL NETOPS_APPLICATION_DB_PASSWORD; \
		$(UV) run --frozen alembic -c $(ALEMBIC_CONFIG) upgrade head

test-rls: test-migrate check-tools check-locks ## Run real PostgreSQL tenant-isolation adversarial tests on the isolated test database.

	@set -a; . "./$(ENV_FILE)"; set +a; \
		NETOPS_RLS_OWNER_DATABASE_URL="postgresql+psycopg://$${POSTGRES_TEST_USER:-netops_test}:$$POSTGRES_PASSWORD@127.0.0.1:$${POSTGRES_TEST_PORT:-5433}/$${POSTGRES_TEST_DB:-netops_test}"; \
		NETOPS_RLS_TEST_DATABASE_URL="postgresql+psycopg://netops_app:$$POSTGRES_APP_PASSWORD@127.0.0.1:$${POSTGRES_TEST_PORT:-5433}/$${POSTGRES_TEST_DB:-netops_test}"; \
		export NETOPS_RLS_OWNER_DATABASE_URL NETOPS_RLS_TEST_DATABASE_URL; \
		$(UV) run --frozen pytest -m integration services/api/tests/integration/test_tenant_rls.py

db-backup: env ## Create a custom-format application DB backup at BACKUP_FILE (default: tmp/backups/netops-local.dump).

	@mkdir -p "$(dir $(BACKUP_FILE))"
	@$(COMPOSE) --env-file $(ENV_FILE) --profile core exec -T postgres sh -ec 'PGPASSWORD="$$POSTGRES_PASSWORD" exec pg_dump --format=custom --no-owner --no-privileges --username "$$POSTGRES_USER" "$$POSTGRES_DB"' > "$(BACKUP_FILE)"
	@printf '%s\n' "Created local application database backup at $(BACKUP_FILE)."

db-restore: env ## Restore BACKUP_FILE into local application DB; requires CONFIRM_LOCAL_RESTORE=1.

	@[ "$$CONFIRM_LOCAL_RESTORE" = "1" ] || { printf '%s\n' "Refusing to overwrite local data. Re-run with CONFIRM_LOCAL_RESTORE=1." >&2; exit 2; }
	@test -f "$(BACKUP_FILE)" || { printf '%s\n' "Backup file not found: $(BACKUP_FILE)" >&2; exit 2; }
	@$(COMPOSE) --env-file $(ENV_FILE) --profile core exec -T postgres sh -ec 'PGPASSWORD="$$POSTGRES_PASSWORD" psql --username "$$POSTGRES_USER" --dbname "$$POSTGRES_DB" --set ON_ERROR_STOP=1 --command "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();"'
	@$(COMPOSE) --env-file $(ENV_FILE) --profile core exec -T postgres sh -ec 'PGPASSWORD="$$POSTGRES_PASSWORD" exec pg_restore --clean --if-exists --no-owner --no-privileges --username "$$POSTGRES_USER" --dbname "$$POSTGRES_DB"' < "$(BACKUP_FILE)"
	@printf '%s\n' "Restored local application database from $(BACKUP_FILE)."

seed: ## Fail until M1/M2 provide an explicit, tenant-safe development seed path.

	@printf '%s\n' "Seeding is not available: no tenant-safe persistence seed contract exists yet." >&2
	@exit 1
