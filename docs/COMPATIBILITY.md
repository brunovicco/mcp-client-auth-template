# Compatibility

This repository treats supported Python, MCP SDK, authorization, transport, and pair
interoperability as executable contracts. Package metadata is the public source of truth, while
CI verifies both the support floor and the moving compatible edge.

## Supported contract

| Dimension | Supported policy | CI evidence |
| --- | --- | --- |
| Python | `>=3.13,<3.15` | Python 3.13 and 3.14 matrix cells |
| MCP Python SDK | `>=2.0,<3` | `minimum` and `latest` profiles |
| MCP SDK support floor | `2.0.0` | Exact `mcp==2.0.0` installation |
| MCP SDK upper boundary | `<3` | Latest resolver constrained to MCP 2.x |
| MCP protocol reference profile | `2026-07-28` | Versioned pair contract + client-owned E2E |
| Transport | Streamable HTTP | Production and loopback profiles |
| Auth providers | Entra ID, generic OIDC | Provider/transport matrix |

All four Python × MCP-profile cells must pass the repository test suite.

## Two different CI guarantees

`quality.yml` remains deterministic: it validates the exact dependency graph recorded in
`uv.lock`. `compatibility.yml` intentionally mutates only the disposable CI virtual environment
after the locked sync:

- `minimum` installs exactly MCP SDK 2.0.0;
- `latest` upgrades MCP to the newest version resolvable by `mcp>=2.0,<3`;
- the lockfile is never rewritten by the compatibility workflow;
- tests run through `.venv/bin/python`, so `uv run` cannot resynchronize MCP back to the lock.

The compatibility workflow runs for pull requests, pushes to `main`, manual dispatches, and
weekly. The scheduled run detects drift caused by newly published compatible MCP 2.x releases.

## Local Python/MCP verification

Support floor:

```bash
uv sync --frozen --all-groups --python 3.13
uv pip install --python .venv/bin/python "mcp==2.0.0"
uv pip check
.venv/bin/python scripts/compatibility_contract.py --python 3.13 --mcp-profile minimum
.venv/bin/python -m pytest --no-cov
```

Moving 2.x edge:

```bash
uv sync --frozen --all-groups --python 3.14
uv pip install --python .venv/bin/python --upgrade "mcp>=2.0,<3"
uv pip check
.venv/bin/python scripts/compatibility_contract.py --python 3.14 --mcp-profile latest
.venv/bin/python -m pytest --no-cov
```

## Auth-provider and transport matrix

CI exposes six independent network-silent configuration cells:

| Provider | Production HTTPS | Loopback IPv4 HTTP | Loopback IPv6 HTTP |
| --- | --- | --- | --- |
| Microsoft Entra ID | supported | explicit local profile | explicit local profile |
| Generic OIDC | supported | explicit local profile | explicit local profile |

Across the paired repositories, unit tests also prove the negative boundary:

- production remains HTTPS-only;
- local HTTP cannot escape loopback;
- the client requires explicit opt-in for HTTP loopback;
- the client redirect listener remains an IP-literal loopback endpoint;
- the server rejects wildcard Host/Origin allowlists;
- production rejects insecure OIDC loopback configuration;
- generic production metadata/issuer configuration remains HTTPS-only.

Loopback profiles exist for local development and deterministic E2E testing. They are not
production deployment profiles.

Local auth/transport verification:

```bash
python scripts/auth_transport_contract.py --provider entra --transport production-https
python scripts/auth_transport_contract.py --provider generic --transport loopback-ipv4
python scripts/auth_transport_contract.py --provider generic --transport loopback-ipv6
```

## Cross-repository compatibility

Both repositories publish the same canonical contract at
`compatibility/cross-repository.json`. The contract fixes the tested pair baseline for MCP
`2026-07-28`, Streamable HTTP, the generic OIDC OAuth 2.1 reference flow, required scope, and
positive/negative evidence.

The client repository owns the live pair check because it initiates OAuth and MCP requests. Its
E2E workflow compares both contracts and exercises Protected Resource Metadata, authorization
server discovery, DCR, PKCE S256, resource-bound token exchange, `server/discover`, and
`tools/call`, plus fail-closed issuer/audience/expiry/scope and RFC 9207 mismatch cases.

DCR is retained only as a backwards-compatible generic-OIDC reference path. MCP `2026-07-28`
prefers Client ID Metadata Documents or pre-registration for new integrations.

Local pair verification:

```bash
python scripts/cross_repository_contract.py --peer-root ../mcp-server-auth-template
```

## Scope

The published compatibility claims cover the executable Python/MCP SDK, auth/transport, and
cross-repository reference profiles above. Provider-specific live identity-provider
interoperability remains outside this deterministic local E2E contract.
