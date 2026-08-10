#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${MCP_COMPOSE_PROJECT_NAME:-mcp-auth-reference-demo}"

docker compose   --project-name "$PROJECT_NAME"   --file "${ROOT}/compose.reference-demo.yml"   --file "${ROOT}/compose.observability.yml"   down --volumes --remove-orphans
