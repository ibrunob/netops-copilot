#!/bin/sh

# Verify the locally running core stack without printing secret environment values.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"

setting_or_default() {
  key=$1
  default_value=$2
  value=$(awk -F= -v key="$key" '$1 == key { value = substr($0, length(key) + 2) } END { print value }' "$ENV_FILE")
  printf '%s' "${value:-$default_value}"
}

container_id_for() {
  docker compose --env-file "$ENV_FILE" --profile core ps --all --quiet "$1"
}

require_healthy_service() {
  service=$1
  container_id=$(container_id_for "$service")
  if [ -z "$container_id" ]; then
    printf '%s\n' "Missing core service container: $service" >&2
    exit 1
  fi

  state=$(docker inspect --format '{{.State.Status}}' "$container_id")
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")
  if [ "$state" != "running" ] || [ "$health" != "healthy" ]; then
    printf '%s\n' "Core service is not healthy: $service (state=$state, health=$health)" >&2
    exit 1
  fi
}

require_completed_service() {
  service=$1
  container_id=$(container_id_for "$service")
  if [ -z "$container_id" ]; then
    printf '%s\n' "Missing one-shot core service container: $service" >&2
    exit 1
  fi

  state=$(docker inspect --format '{{.State.Status}}' "$container_id")
  exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$container_id")
  if [ "$state" != "exited" ] || [ "$exit_code" != "0" ]; then
    printf '%s\n' "One-shot core service did not complete successfully: $service" >&2
    exit 1
  fi
}

if [ ! -f "$ENV_FILE" ]; then
  printf '%s\n' "Missing .env. Run 'make env' first." >&2
  exit 1
fi

command -v curl >/dev/null || {
  printf '%s\n' "curl is required to verify live HTTP endpoints." >&2
  exit 1
}

cd "$ROOT_DIR"

make check-locks
docker compose --env-file "$ENV_FILE" --profile core config --quiet

for service in api web postgres redis minio temporal temporal-ui keycloak; do
  require_healthy_service "$service"
done
require_completed_service minio-init

api_port=${NETOPS_PORT:-$(setting_or_default NETOPS_PORT 8000)}
web_port=${WEB_PORT:-$(setting_or_default WEB_PORT 3000)}

curl --fail --silent --show-error --retry 12 --retry-delay 1 "http://127.0.0.1:${api_port}/healthz" \
  | grep -q '"status":"ok"'
curl --fail --silent --show-error --retry 12 --retry-delay 1 "http://127.0.0.1:${api_port}/readyz" \
  | grep -q '"application":"ready"'
curl --fail --silent --show-error --retry 12 --retry-delay 1 "http://127.0.0.1:${web_port}/" \
  | grep -q 'NetOps Copilot'

printf '%s\n' "Local core verification passed: all core services are healthy and MinIO initialization completed."
