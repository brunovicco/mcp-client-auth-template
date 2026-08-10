#!/usr/bin/env bash
set -Eeuo pipefail

CLIENT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_ROOT="${MCP_DEMO_SERVER_ROOT:-${CLIENT_ROOT}/../mcp-server-auth-template}"
DEMO_JSON=false

while (($#)); do
  case "$1" in
    --server-root)
      [[ $# -ge 2 ]] || {
        echo "ERROR: --server-root requires a path" >&2
        exit 2
      }
      SERVER_ROOT="$2"
      shift 2
      ;;
    --json)
      DEMO_JSON=true
      shift
      ;;
    *)
      echo "ERROR: unsupported argument: $1" >&2
      echo "Supported arguments: --server-root PATH, --json" >&2
      exit 2
      ;;
  esac
done

command -v uv >/dev/null 2>&1 || {
  echo "ERROR: uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
}

SERVER_ROOT="$(cd "$SERVER_ROOT" 2>/dev/null && pwd)" || {
  echo "ERROR: companion server checkout not found: $SERVER_ROOT" >&2
  echo "Clone mcp-server-auth-template beside this repository or pass --server-root PATH." >&2
  exit 1
}

[[ -f "${SERVER_ROOT}/pyproject.toml" ]] || {
  echo "ERROR: invalid companion server root: ${SERVER_ROOT}" >&2
  exit 1
}

cd "$CLIENT_ROOT"

echo "==> Preparing locked client environment" >&2
uv sync --frozen --all-groups

echo "==> Installing the local companion server into the client demo environment" >&2
uv pip install --python .venv/bin/python -e "$SERVER_ROOT"

echo "==> Running P1.7a headless reference demo" >&2
if [[ "$DEMO_JSON" == "true" ]]; then
  exec .venv/bin/python scripts/reference_demo.py --server-root "$SERVER_ROOT" --json
fi

exec .venv/bin/python scripts/reference_demo.py --server-root "$SERVER_ROOT"
