# Contributing

## Setup

```bash
uv lock --check
uv sync --frozen --all-groups --extra observability
```

Installing the `observability` extra is not optional for local development: `test_logging.py`
and `test_observability.py` skip without it, and the suite then fails the 80% coverage gate.

## Before opening a PR

Run the full quality gate — it's the same one CI runs:

```bash
uv run python scripts/quality_gate.py
```

Use `--list` to see the individual checks (lint, format, typing, tests, security, architecture,
MCP config, governance, dependencies) or `--check NAME` to run one in isolation while iterating.
Run a single test with `uv run pytest tests/unit/test_entra_client_auth.py::test_name`.

## Expectations

- Keep the dependency direction `entrypoints -> application -> domain`, `adapters ->
  application/domain`; `scripts/validate_architecture.py` enforces it.
- Full type hints; mypy runs in `strict` mode.
- Add or update tests for any behavior change — this repo treats coverage as a floor (80%), not
  a target.
- No secrets, tokens, or production personal data in code, tests, fixtures, or commit messages.
- Keep commits focused and the diff free of unrelated changes.

See `CLAUDE.md` and `AGENTS.md` for the full engineering contract this repository follows,
including the conventions AI coding agents working in this repo are expected to respect.

## Questions

Open an issue for anything that doesn't fit a straightforward PR — architecture questions,
proposed provider adapters (a third authorization-server shape beyond Entra ID / generic OIDC),
or anything that touches the sibling
[`mcp-server-auth-template`](https://github.com/brunovicco/mcp-server-auth-template) repo.
