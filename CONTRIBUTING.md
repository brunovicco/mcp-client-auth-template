# Contributing

## Setup

```bash
uv lock --check
uv sync --frozen --all-groups
```

## Before opening a PR

Run the complete quality gate:

```bash
uv run python scripts/quality_gate.py
```

Use `--list` to inspect the available checks or `--check NAME` for focused feedback while
iterating. Run the full gate before requesting review.

For changes that affect the executable reference path, also run the relevant demo:

```bash
./scripts/run_reference_demo.sh --server-root /path/to/mcp-server-auth-template
./scripts/run_compose_demo.sh
./scripts/run_observability_demo.sh
```

## Engineering expectations

- Preserve dependency direction: `entrypoints -> application -> domain`; adapters may depend on
  application/domain, while domain never depends on outer layers.
- Keep complete type hints and strict Mypy. Parse untrusted `Any` values at boundaries.
- Do not use `from __future__ import annotations`; quote only individual forward references that
  genuinely require deferred evaluation.
- Validate external input and add explicit timeouts to external calls. Retry only bounded transient
  operations and preserve idempotency for externally visible effects.
- Add or update behavior-focused tests for every material behavior change. Coverage is a floor
  (80%), not a target.
- Never commit secrets, tokens, cookies, private keys, production personal data, or sensitive trace
  payloads.
- Keep logs/traces metadata-only where required by the observability policy.
- Keep commits focused and the diff free of unrelated changes.
- Keep editor and coding-agent state local. Tool-specific directories are intentionally ignored and
  must not become project dependencies.

See [Development](docs/DEVELOPMENT.md), [Architecture](docs/ARCHITECTURE.md), and the ADRs for the
project-owned engineering contract.

## Questions

Open an issue for architecture changes, new provider shapes, or changes that affect the companion
[`mcp-server-auth-template`](https://github.com/brunovicco/mcp-server-auth-template).
