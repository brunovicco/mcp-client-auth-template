# Cross-repository OAuth E2E

The unit suites deliberately keep identity providers and network services out of the normal
quality gate. The dedicated E2E suite closes the remaining integration gap by running the real
`mcp-client-auth-template` against the real sibling `mcp-server-auth-template`, with only the
external authorization server replaced by a deterministic localhost OIDC test double.

## What the happy path proves

`tests/e2e/test_companion_auth_flow.py` starts two local processes on ephemeral ports:

1. `scripts/e2e_fake_oidc_as.py`, a minimal OIDC authorization server with discovery, JWKS,
   Dynamic Client Registration, authorization code + PKCE, RFC 9207 `iss`, and JWT issuance;
2. the companion repository's actual `MCPServer` ASGI app in generic OIDC mode.

The client then uses the same `build_oauth_provider` and `build_mcp_client` functions as the demo.
The successful test crosses the complete boundary:

```text
server/discover (no token)
  -> 401 + WWW-Authenticate
  -> Protected Resource Metadata
  -> OIDC discovery
  -> Dynamic Client Registration
  -> authorization code + PKCE
  -> RFC 9207 issuer validation
  -> resource-bound JWT
  -> authenticated server/discover
  -> MCP 2026-07-28 tools/call whoami
```

It also asserts that the negotiated protocol is exactly `2026-07-28`, that DCR/authorization/token
exchange each happened once, and that the persisted SDK client registration is issuer-bound.

The browser and RFC 8252 loopback listener are intentionally not exercised here. The E2E redirect
handler requests the local `/authorize` endpoint and hands the resulting callback parameters to the
SDK directly, keeping the test deterministic and headless. The loopback adapter has its own unit
coverage.

## Fail-closed matrix

The same running companion server is checked with deliberately malformed tokens minted by the fake
AS:

| Case | Expected result |
| --- | --- |
| wrong `aud` | `401` |
| wrong `iss` | `401` |
| expired JWT | `401` |
| missing required scope | `403` + `insufficient_scope` challenge |
| persisted client registration bound to another AS | old registration discarded and DCR repeated |
| wrong RFC 9207 authorization-response `iss` | OAuth flow rejected before token exchange |

## Run locally

Install the client environment, add the sibling server package to that environment, and opt into the
E2E suite with `MCP_E2E_SERVER_ROOT`:

```bash
uv sync --frozen --all-groups
uv pip install --python .venv/bin/python -e ../mcp-server-auth-template
MCP_E2E_SERVER_ROOT=../mcp-server-auth-template \
  .venv/bin/python -m pytest -m e2e tests/e2e/test_companion_auth_flow.py --no-cov
```

The default `uv run pytest` remains self-contained: without `MCP_E2E_SERVER_ROOT`, this module is
skipped. GitHub Actions runs the dedicated `.github/workflows/e2e.yml`, which checks out the
companion server and enables the variable explicitly.
