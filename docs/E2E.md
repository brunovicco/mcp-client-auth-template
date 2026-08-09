# Cross-repository OAuth E2E

The unit suites deliberately keep identity providers and network services out of the normal
quality gate. The dedicated E2E suite closes the remaining integration gap by running the real
`mcp-client-auth-template` against the real sibling `mcp-server-auth-template`, with only the
external authorization server replaced by a deterministic localhost OIDC test double.

## What the happy path proves

`tests/e2e/test_companion_auth_flow.py` starts two local processes on ephemeral ports:

1. `scripts/e2e_fake_oidc_as.py`, a minimal OIDC authorization server with discovery, JWKS,
   Client ID Metadata Document advertisement, legacy Dynamic Client Registration, authorization
   code + PKCE, RFC 9207 `iss`, pre-registered client credentials, and JWT issuance;
2. the companion repository's actual `MCPServer` ASGI app in generic OIDC mode.

The client then uses the same `build_oauth_provider` and `build_mcp_client` functions as the demo.
The preferred CIMD profile crosses the complete boundary:

```text
server/discover (no token)
  -> 401 + WWW-Authenticate
  -> Protected Resource Metadata
  -> OIDC discovery
  -> Client ID Metadata Document URL selected as client_id
  -> no Dynamic Client Registration request
  -> authorization code + PKCE
  -> RFC 9207 issuer validation
  -> resource-bound JWT
  -> authenticated server/discover
  -> MCP 2026-07-28 tools/call whoami
  -> tools/call health with the basic token
  -> 403 insufficient_scope before tool dispatch
  -> reauthorization for basic + mcp:tools:health
  -> one SDK replay with the elevated token
  -> successful health result
```

It asserts that the negotiated protocol is exactly `2026-07-28`, that the authorization and token
exchange each happened once, that DCR was not called, and that the stored public-client record uses
the configured HTTPS URL with no client secret. The fake AS treats the remote metadata document as
pre-validated fixture data; the client never fetches its own document, matching the SDK contract.

A separate backwards-compatibility profile leaves CIMD unadvertised and proves that DCR,
authorization, and token exchange each happen once and that the resulting client registration is
bound to the authorization-server issuer.

The same suite makes the MCP 2026 request envelope executable. A valid call proves that the
protocol version in `MCP-Protocol-Version` and `params._meta`, `Mcp-Method`, and `Mcp-Name` agree.
It also sends a legacy-looking `Mcp-Session-Id` and proves that the server neither returns a session
identifier nor uses it as authorization state.

The runtime step-up profile uses the same SDK OAuth provider as the demo. It proves that the first
token contains only `mcp:tools:call`, the `health` challenge causes exactly one additional
authorization and token exchange, the second authorization requests
`mcp:tools:call mcp:tools:health`, and the stored elevated token retains both permissions. This
OAuth recovery replay is distinct from a generic tool retry: the companion server rejects the
first attempt during bearer verification, before the MCP handler can run.

The non-interactive profile uses the SDK's `ClientCredentialsOAuthProvider`. It proves the draft
extension capability in `server/discover`, HTTP Basic client authentication, resource-bound token
acquisition, machine `client_id`/`subject`, and scope step-up. Its aggregate counters prove zero
authorization requests and zero registrations; no browser/callback harness is constructed, and no
client credential is written to token storage.

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
| invalid machine client secret | token acquisition rejected; credential absent from exception |
| `Mcp-Method` or `Mcp-Name` disagrees with the request body | `400` + JSON-RPC `-32020` |
| coherent but unsupported protocol version | `400` + JSON-RPC `-32022` with version data |
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
