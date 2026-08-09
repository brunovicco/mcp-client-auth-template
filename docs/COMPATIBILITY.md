# Compatibility

This repository treats supported Python and MCP SDK versions as an executable contract.
The package metadata remains the public source of truth, while CI verifies both the support
floor and the newest resolvable SDK inside the declared major-version range.

## Supported contract

| Dimension | Supported policy | CI evidence |
| --- | --- | --- |
| Python | `>=3.13,<3.15` | Python 3.13 and 3.14 matrix cells |
| MCP Python SDK | `>=2.0,<3` | `minimum` and `latest` profiles |
| MCP SDK support floor | `2.0.0` | Exact `mcp==2.0.0` installation |
| MCP SDK upper boundary | `<3` | Latest resolver is constrained to MCP 2.x |

All four Python × MCP-profile cells must pass the repository test suite.

## Two different CI guarantees

`quality.yml` remains deterministic: it validates the exact dependency graph recorded in
`uv.lock`. `compatibility.yml` intentionally mutates only the disposable CI virtual environment
after the locked sync:

- `minimum` installs exactly MCP SDK 2.0.0;
- `latest` upgrades MCP to the newest version resolvable by `mcp>=2.0,<3`;
- the lockfile is never rewritten;
- tests run through `.venv/bin/python`, so `uv run` cannot resynchronize MCP back to the lock.

The compatibility workflow runs for pull requests, pushes to `main`, manual dispatches, and
weekly. The scheduled run is the drift detector for a newly published compatible MCP 2.x release.

## Local verification

For the support floor:

```bash
uv sync --frozen --all-groups --python 3.13
uv pip install --python .venv/bin/python "mcp==2.0.0"
uv pip check
.venv/bin/python scripts/compatibility_contract.py --python 3.13 --mcp-profile minimum
.venv/bin/python -m pytest --no-cov
```

For the moving 2.x edge:

```bash
uv sync --frozen --all-groups --python 3.14
uv pip install --python .venv/bin/python --upgrade "mcp>=2.0,<3"
uv pip check
.venv/bin/python scripts/compatibility_contract.py --python 3.14 --mcp-profile latest
.venv/bin/python -m pytest --no-cov
```


## Auth-provider and transport matrix

P1.3b extends the executable compatibility contract across two authorization modes and three
transport profiles. CI exposes six independent cells:

| Provider | Production HTTPS | Loopback IPv4 HTTP | Loopback IPv6 HTTP |
| --- | --- | --- | --- |
| Microsoft Entra ID | required | explicit local profile | explicit local profile |
| Generic OIDC | required | explicit local profile | explicit local profile |

The positive cells are network-silent configuration contracts. They prove that supported
provider/transport combinations can be constructed without weakening production defaults.

The unit suite also proves the negative boundary:

- production remains HTTPS-only;
- local HTTP cannot escape loopback;
- the client requires `oauth_allow_insecure_loopback=true` for HTTP;
- the client redirect listener remains an IP-literal loopback endpoint;
- the server keeps wildcard Host/Origin allowlists invalid;
- production rejects `oidc_allow_insecure_loopback=true`;
- generic production metadata/issuer configuration remains HTTPS-only.

The loopback profiles exist for local development and E2E testing. They are not production
deployment profiles.

## Local auth/transport verification

Each supported profile can be verified without DNS or HTTP:

```bash
python scripts/auth_transport_contract.py --provider entra --transport production-https
python scripts/auth_transport_contract.py --provider generic --transport loopback-ipv4
python scripts/auth_transport_contract.py --provider generic --transport loopback-ipv6
```

## Cross-repository compatibility

P1.3c adds a versioned pair contract at `compatibility/cross-repository.json`. Both repositories
must publish the same canonical contract for MCP `2026-07-28`, Streamable HTTP, the generic
OIDC OAuth 2.1 E2E profile, required scope, and the positive/negative evidence expected from the
pair.

Local verification:

```bash
python scripts/cross_repository_contract.py
```

The client repository owns the live pair check because it initiates OAuth and MCP requests. Its
E2E workflow checks out `mcp-server-auth-template` from `main`, compares both contracts, and then
runs the existing cross-repository suite. That suite exercises protected-resource metadata,
authorization-server discovery, DCR, PKCE S256, resource-bound token exchange, MCP discovery,
and `tools/call`, plus fail-closed issuer/audience/expiry/scope and RFC 9207 mismatch cases.

DCR is retained as a tested generic-OIDC reference path. It is not presented as the preferred
client-registration mechanism of the 2026-07-28 revision.

For a local pair checkout, validate contract equality with:

```bash
python scripts/cross_repository_contract.py --peer-root ../mcp-server-auth-template
```

When merging P1.3, merge the server repository first so the client workflow can compare against
the peer contract already published on `server/main`.

## Scope

The repository now claims executable Python/MCP SDK, auth/transport, and cross-repository
compatibility for the versioned reference profile. Provider-specific live identity-provider
interoperability remains outside this deterministic local E2E contract.
