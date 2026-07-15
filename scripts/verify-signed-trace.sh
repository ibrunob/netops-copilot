#!/bin/sh

# Prove the local OIDC-to-Tempo path without printing or persisting an access
# token. Obtain NETOPS_ACCESS_TOKEN through the Keycloak PKCE flow described in
# services/api/AUTHENTICATION.md, then run this after `make up-observability`.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"

setting_or_default() {
  key=$1
  default_value=$2
  value=$(awk -F= -v key="$key" '$1 == key { value = substr($0, length(key) + 2) } END { print value }' "$ENV_FILE")
  printf '%s' "${value:-$default_value}"
}

if [ ! -f "$ENV_FILE" ]; then
  printf '%s\n' "Missing .env. Run 'make env' first." >&2
  exit 1
fi

if [ -z "${NETOPS_ACCESS_TOKEN:-}" ]; then
  printf '%s\n' "NETOPS_ACCESS_TOKEN must contain a temporary Keycloak access token." >&2
  exit 2
fi

case "$NETOPS_ACCESS_TOKEN" in
  *[!A-Za-z0-9._~-]*)
    printf '%s\n' "NETOPS_ACCESS_TOKEN is not a valid compact access-token value." >&2
    exit 2
    ;;
esac

command -v curl >/dev/null || {
  printf '%s\n' "curl is required to verify the signed trace." >&2
  exit 1
}
command -v openssl >/dev/null || {
  printf '%s\n' "openssl is required to create a W3C trace ID." >&2
  exit 1
}

api_port=${NETOPS_PORT:-$(setting_or_default NETOPS_PORT 8000)}
tempo_port=${TEMPO_HTTP_PORT:-$(setting_or_default TEMPO_HTTP_PORT 3200)}
trace_id=$(openssl rand -hex 16)
span_id=$(openssl rand -hex 8)
traceparent="00-${trace_id}-${span_id}-01"
headers_file=$(mktemp)
body_file=$(mktemp)
curl_config=$(mktemp)
chmod 600 "$curl_config"
trap 'rm -f "$headers_file" "$body_file" "$curl_config"' EXIT HUP INT TERM

# Keep the token out of the curl command line and remove this short-lived,
# owner-readable config file on every exit path.
{
  printf '%s\n' "header = \"Authorization: Bearer ${NETOPS_ACCESS_TOKEN}\""
  printf '%s\n' "header = \"traceparent: ${traceparent}\""
} > "$curl_config"

curl --config "$curl_config" --fail --silent --show-error \
  --dump-header "$headers_file" \
  --output "$body_file" \
  "http://127.0.0.1:${api_port}/v1/auth/me"

if ! tr -d '\r' < "$headers_file" | grep -qi "^traceparent: 00-${trace_id}-"; then
  printf '%s\n' "API response did not retain the W3C trace ID." >&2
  exit 1
fi

attempt=1
while [ "$attempt" -le 30 ]; do
  if curl --fail --silent --show-error \
    "http://127.0.0.1:${tempo_port}/api/traces/${trace_id}" >/dev/null 2>&1; then
    printf '%s\n' "Signed OIDC trace was found in Tempo: ${trace_id}"
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 1
done

printf '%s\n' "Timed out waiting for signed trace ${trace_id} in Tempo." >&2
exit 1
