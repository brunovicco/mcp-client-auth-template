# Development guide

## Setup

```bash
uv sync --frozen --all-groups --extra observability
```

The `observability` extra installs the optional OpenTelemetry runtime; without it,
`tests/unit/test_observability.py` and `tests/unit/test_logging.py` skip via `pytest.importorskip`
and project-wide coverage falls under the required 80%. CI always installs it (see
`.github/workflows/quality.yml`) — match that locally. Its tests use in-memory exporters or fakes;
do not point development or CI checks at a real collector.

## Run checks

```bash
uv run python scripts/quality_gate.py
```

## Container

```bash
docker build -t mcp-client-auth-template .
docker run --rm mcp-client-auth-template
```

`Dockerfile` is a multi-stage, uv-based build: a `builder` stage installs the locked
dependencies and builds the package, then only the resulting virtualenv and source are copied
into a slim, non-root runtime image. The shipped `CMD` runs the demo entrypoint (`python -m
mcp_client_auth_template.entrypoints.demo_client`); provider configuration (`MCP_CLIENT_*`
variables, see `.env.example`) is supplied at container-run time via the environment, never
baked into the image. Adjust `.dockerignore` if new top-level files or directories need to be
excluded from the build context.

## Local configuration

Copy `.env.example` only when the application supports local dotenv loading. Never commit `.env` or real credentials.

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
