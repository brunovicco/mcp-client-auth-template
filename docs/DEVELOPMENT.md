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

## Reference demos

P1.7a uses the real companion server checkout:

```bash
./scripts/run_reference_demo.sh --server-root /path/to/mcp-server-auth-template
```

P1.7b is containerized and consumes the published Server `v0.5.0` image by immutable digest:

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
