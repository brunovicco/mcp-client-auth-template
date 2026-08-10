#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_FILE="${ROOT}/compose.reference-demo.yml"
OBS_FILE="${ROOT}/compose.observability.yml"
PROJECT_NAME="${MCP_COMPOSE_PROJECT_NAME:-mcp-auth-reference-demo}"
RECEIPT_DIR="${ROOT}/.demo-observability"
RECEIPT_FILE="${RECEIPT_DIR}/traces.jsonl"
KEEP=false

if [[ $# -gt 1 ]]; then
  echo "ERROR: supported usage: $0 [--keep]" >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  if [[ "$1" != "--keep" ]]; then
    echo "ERROR: unsupported argument: $1" >&2
    exit 2
  fi
  KEEP=true
fi

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: Docker is required" >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "ERROR: Docker Compose v2 is required" >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "ERROR: Docker daemon is not available" >&2
  exit 1
}

export MCP_DEMO_UID="$(id -u)"
export MCP_DEMO_GID="$(id -g)"
export MCP_DEMO_SERVER_IMAGE="ghcr.io/brunovicco/mcp-server-auth-template@sha256:39d50ff235df634ef6c4b0d8a4cdef4c4c3be00094fce464eabafea88f216d9a"

compose() {
  docker compose     --project-name "$PROJECT_NAME"     --file "$BASE_FILE"     --file "$OBS_FILE"     "$@"
}

cleanup() {
  status=$?
  trap - EXIT INT TERM

  if [[ "$KEEP" == "true" ]]; then
    printf '\nP1.7c stack preserved after failure for diagnostics.\n' >&2
    printf 'Grafana: http://127.0.0.1:3000\n' >&2
    printf 'Stop:    ./scripts/stop_observability_demo.sh\n' >&2
  else
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi

  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$ROOT"

printf '==> P1.7c observable reference demo\n'
printf '    Collector: OTLP/HTTP + positive receipt\n'
printf '    Tempo:     local trace backend\n'
printf '    Grafana:   http://127.0.0.1:3000\n'

printf '==> Validating merged Compose model\n'
compose config --quiet

printf '==> Removing stale stack before touching Collector evidence\n'
compose down --volumes --remove-orphans >/dev/null 2>&1 || true

printf '==> Preparing fresh Collector receipt\n'
mkdir -p "$RECEIPT_DIR"
chmod 0777 "$RECEIPT_DIR"
rm -f "$RECEIPT_FILE"
: > "$RECEIPT_FILE"
chmod 0666 "$RECEIPT_FILE"
printf '==> Pulling immutable runtime images\n'
compose pull server tempo collector grafana

printf '==> Building local OIDC/demo/verifier images\n'
compose build oidc demo verifier

printf '==> Starting Server + OIDC + Collector + Tempo + Grafana\n'
compose up -d server oidc tempo collector grafana

SERVER_CONTAINER_ID="$(compose ps -q server)"
COLLECTOR_CONTAINER_ID="$(compose ps -q collector)"
TEMPO_CONTAINER_ID="$(compose ps -q tempo)"
GRAFANA_CONTAINER_ID="$(compose ps -q grafana)"

if [[ -z "$SERVER_CONTAINER_ID" || -z "$COLLECTOR_CONTAINER_ID" || -z "$TEMPO_CONTAINER_ID" || -z "$GRAFANA_CONTAINER_ID" ]]; then
  printf 'P1.7c failed to capture initial observability container identities.\n' >&2
  exit 1
fi

assert_stack_identity() {
  current_server="$(compose ps -q server)"
  current_collector="$(compose ps -q collector)"
  current_tempo="$(compose ps -q tempo)"
  current_grafana="$(compose ps -q grafana)"

  if [[ "$current_server" != "$SERVER_CONTAINER_ID" || "$current_collector" != "$COLLECTOR_CONTAINER_ID" || "$current_tempo" != "$TEMPO_CONTAINER_ID" || "$current_grafana" != "$GRAFANA_CONTAINER_ID" ]]; then
    printf 'P1.7c observability stack identity changed during the reference flow.\n' >&2
    printf 'Refusing to verify traces across different container generations.\n' >&2
    exit 1
  fi
}

printf '==> Waiting for observability stack readiness\n'
compose run --rm --no-deps verifier python -m scripts.verify_observability_demo --wait-stack
assert_stack_identity

printf '==> Running traced OAuth/MCP reference scenario\n'
compose run --rm --no-deps demo
assert_stack_identity

printf '==> Verifying receipt, context continuity, Tempo and Grafana\n'
assert_stack_identity
compose run --rm --no-deps verifier python -m scripts.verify_observability_demo --verify-traces
assert_stack_identity

if [[ "$KEEP" == "true" ]]; then
  trap - EXIT INT TERM
  printf '\nP1.7c stack kept running.\n'
  printf 'Grafana: http://127.0.0.1:3000\n'
  printf 'Stop:    ./scripts/stop_observability_demo.sh\n'
else
  printf '==> Cleaning up observability stack\n'
fi
