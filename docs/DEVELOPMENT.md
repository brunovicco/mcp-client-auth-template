# Development guide

## Setup

```bash
uv sync --frozen --all-groups
```

`a2a-otel-kit[mcp]` is a core dependency because the demo always composes its metadata-only
HTTPX2 tracing transport. Export remains network-silent unless `A2A_OTEL_ENABLED=true` and a
complete OTLP traces endpoint are configured. Tests keep tracing disabled or use in-memory
telemetry and never require a collector.

## Run checks

```bash
uv run python scripts/quality_gate.py
```

## Container

```bash
docker build -t mcp-client-auth-template .
docker run --rm \
  --env-file .env \
  mcp-client-auth-template
```

`Dockerfile` is a multi-stage, uv-based build: a `builder` stage installs the locked
dependencies and builds the package, then only the resulting virtualenv and source are copied
into a slim, non-root runtime image. The shipped `CMD` runs the demo entrypoint (`python -m
mcp_client_auth_template.entrypoints.demo_client`); provider configuration (`MCP_CLIENT_*`
variables, see `.env.example`) is supplied at container-run time via the environment, never
baked into the image. Adjust `.dockerignore` if new top-level files or directories need to be
excluded from the build context.

The container command is best suited to the non-interactive `client_credentials` profile. The
interactive profile owns a system-browser handoff plus an RFC 8252 loopback callback and is
therefore intentionally easier to run directly on the host. Any MCP resource or OIDC endpoint in
`.env` must also be reachable from inside the container; a service running on the Docker host is
not container-local `localhost`. Use `host.docker.internal` where supported, add an explicit host
mapping on Linux, or place the services on the same Docker network.

## Local configuration

Copy `.env.example` to `.env` for local development and replace only the provider/profile values
you need. Docker's `--env-file` passes the same variable names into the container. Never commit
`.env` or real credentials.

## Codex

- Run `/status` to inspect the active project and configuration.
- Run `/hooks` to inspect configured hooks.
- Run `codex --version` from the shell for an installation check.
- Use `$plan-change` before complex work.
- Use `$quality-gate` before completion.
- Use `$prepare-pr` to produce a reviewable PR description.

Codex discovers durable project guidance in `AGENTS.md`, workflows in `.agents/skills/`, and
trusted project configuration and hooks under `.codex/`. Skills do not silently delegate work;
the active agent follows their checked-in workflow and the user's requested scope.
