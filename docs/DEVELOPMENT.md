# Development guide

## Setup

```bash
uv lock --check
uv sync --frozen --all-groups
```

The repository targets Python 3.13/3.14 and uses `uv` for the locked development environment.

`a2a-otel-kit[mcp]` is a core dependency because the client composes its metadata-only HTTPX2
tracing transport on the real request path. Export remains network-silent unless
`A2A_OTEL_ENABLED=true` and a complete OTLP traces endpoint are configured.

## Quality gate

Run the complete project-owned gate before finishing a change:

```bash
uv run python scripts/quality_gate.py
```

List or run individual checks with:

```bash
uv run python scripts/quality_gate.py --list
uv run python scripts/quality_gate.py --check tests
uv run python scripts/quality_gate.py --check security
```

The gate covers lock consistency, Ruff, formatting, architecture, supply-chain controls,
governance baseline, vendored loop-schema validation, strict Mypy, pytest/coverage, Bandit and
dependency audit.

### Test layers

| Layer | Location | Runs in the gate | Purpose |
| --- | --- | --- | --- |
| Unit | `tests/unit` | yes | Settings, adapters, policies; hand-built SDK objects allowed only as auxiliary evidence |
| Integration (real wire) | `tests/integration` | yes | Real MCP SDK client and SDK `MCPServer` over loopback sockets, with journaling fake authorization servers |
| Cross-repository E2E | `tests/e2e` | opt-in (`MCP_E2E_SERVER_ROOT`) | The real companion server plus the fake OIDC authorization server |

Protocol, authorization, cache, and interoperability claims need a `tests/integration` or
`tests/e2e` test. `tests/unit/test_wire_evidence_hygiene.py` fails if those suites construct MCP
result objects by hand.

`mcp.MCPDeprecationWarning` is promoted to an error by `pyproject.toml`. Resolve an SDK deprecation;
do not filter it. Other deprecation classes are not promoted.

Tests that need a `private_key_jwt` key write it below the git-ignored `build/` directory
(`tests/key_material.py`). The key loader rejects group/world-writable ancestors, so on Linux
`tmp_path` (under `/tmp`) cannot be used.

To run the E2E suite without touching the project `.venv`, use a disposable environment:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/mcp-e2e-venv uv sync --frozen --all-groups
uv pip install --python /tmp/mcp-e2e-venv/bin/python -e ../mcp-server-auth-template
MCP_E2E_SERVER_ROOT=../mcp-server-auth-template /tmp/mcp-e2e-venv/bin/pytest -m e2e tests/e2e --no-cov
```

## Reference demos

P1.7a uses the real companion server checkout:

```bash
./scripts/run_reference_demo.sh --server-root /path/to/mcp-server-auth-template
```

P1.7b is containerized and consumes a published Server image by immutable digest. It shares the
P1.7a scenario, including the per-scope `tools/list` assertions, which need a server image with the
v0.7.0 `tools/list` fix; the pinned digest is the published server `v0.7.0` image:

```bash
./scripts/run_compose_demo.sh
```

P1.7c adds Collector, Tempo and Grafana and performs positive trace/privacy verification:

```bash
./scripts/run_observability_demo.sh
```

Use `--keep` only when you need to inspect Grafana after a successful run:

```bash
./scripts/run_observability_demo.sh --keep
./scripts/stop_observability_demo.sh
```

## Container

```bash
docker build -t mcp-client-auth-template .
docker run --rm \
  --env-file .env \
  mcp-client-auth-template
```

`Dockerfile` is a multi-stage uv build. The final runtime image is slim and non-root. Provider
configuration is supplied at runtime through environment variables and is never baked into the
image.

The container command is best suited to `client_credentials`. Interactive mode owns a system
browser handoff plus an RFC 8252 loopback callback and is intentionally easier to run directly on
the host.

## Local configuration

Copy `.env.example` to `.env` and set only the provider/profile values required by your scenario:

```bash
cp .env.example .env
```

Never commit `.env` or real credentials.

A service running on the Docker host is not container-local `localhost`; use an explicit host
mapping/network strategy appropriate to your platform.

## Repository hygiene

The public repository contains project-owned source, tests, documentation, CI and executable demo
configuration. Local editor/coding-agent state is ignored (`.codex/`, `.claude/`, `.cursor/`,
`.aider/`, `.agent/`, `.agents/`) and must not become a runtime, test, documentation or CI
dependency.

Temporary observability receipts live under `.demo-observability/` and are never versioned.
Raw screen recordings used to produce documentation assets should also remain outside Git.
