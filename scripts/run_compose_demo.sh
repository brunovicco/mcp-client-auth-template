#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/compose.reference-demo.yml"
PROJECT_NAME="${MCP_COMPOSE_PROJECT_NAME:-mcp-auth-reference-demo}"
DEFAULT_SERVER_IMAGE="ghcr.io/brunovicco/mcp-server-auth-template@sha256:39d50ff235df634ef6c4b0d8a4cdef4c4c3be00094fce464eabafea88f216d9a"
SERVER_IMAGE="${MCP_DEMO_SERVER_IMAGE:-$DEFAULT_SERVER_IMAGE}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || die "Docker is required"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || die "Docker daemon is not available"

case "$SERVER_IMAGE" in
  *@sha256:????????????????????????????????????????????????????????????????)
    ;;
  *)
    die "MCP_DEMO_SERVER_IMAGE must be immutable: registry/repository@sha256:<64 hex>"
    ;;
esac

DIGEST="${SERVER_IMAGE##*@sha256:}"
case "$DIGEST" in
  *[!0-9a-f]*)
    die "MCP_DEMO_SERVER_IMAGE digest must contain lowercase hexadecimal characters only"
    ;;
esac

export MCP_DEMO_SERVER_IMAGE="$SERVER_IMAGE"

compose() {
  docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE" "$@"
}

cleanup() {
  status=$?
  trap - EXIT INT TERM
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$ROOT"

printf '==> P1.7b Docker Compose reference demo\n'
printf '    server: %s\n' "$MCP_DEMO_SERVER_IMAGE"
printf '    client: local source build\n'
printf '    host ports: none\n'

printf '==> Validating Compose model\n'
compose config --quiet

printf '==> Removing stale demo state\n'
compose down --volumes --remove-orphans >/dev/null 2>&1 || true

printf '==> Pulling immutable public Server v0.5.0 image\n'
compose pull server

printf '==> Building client and deterministic fake OIDC images\n'
compose build oidc demo

printf '==> Running client + server + fake OIDC\n'
compose up --abort-on-container-exit --exit-code-from demo --remove-orphans
