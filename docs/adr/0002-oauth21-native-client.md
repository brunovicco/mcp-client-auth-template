# ADR-0002: Native OAuth 2.1 client via the official SDK's `OAuthClientProvider`

- Status: Accepted
- Date: 2026-08-08

## Context

This is the client-side half of the pattern documented in the companion server
template's `docs/adr/0002-oauth21-resource-server.md`: an MCP client (here, a CLI
demo script, but the same pieces work inside any Python MCP client) that
authenticates against either Microsoft Entra ID or any standards-compliant OIDC
authorization server, following the MCP 2026-07-28 authorization specification.

As a native/CLI application (RFC 8252), this client has no web origin to receive
an authorization redirect at, cannot keep a client secret confidential, and - for
Entra specifically - cannot use Dynamic Client Registration or Client ID Metadata
Documents (CIMD) at all, since Entra supports neither; it can only use a client
already registered out of band in the Entra portal.

## Decision

- Depend on the official `mcp` Python SDK v2 (`mcp>=2.0,<3`). Its
  `mcp.client.auth.OAuthClientProvider` is an `httpx2.Auth` plugin that already
  implements Protected Resource Metadata discovery, authorization-server metadata
  discovery, PKCE (S256), CIMD-first registration with automatic Dynamic Client
  Registration fallback, RFC 8707 resource binding, token refresh, RFC 9207 issuer
  validation, and `insufficient_scope` step-up re-authorization. This repository
  does not reimplement any of that - the same principle the server template
  applies to `mcp.server.mcpserver.MCPServer`.
- The code this template owns is the handful of I/O adapters the SDK asks the
  embedding application to supply, because they are inherently
  environment-specific: where to persist tokens (`adapters/token_storage.py` -
  in-memory or a permissions-locked local JSON file), how to show the user an
  authorization URL (`adapters/browser_redirect.py`), and how to receive the
  redirect back (`adapters/loopback_callback_server.py` - a one-shot RFC 8252
  loopback HTTP listener).
- Two provider-specific factories mirror the server template's two
  `TokenVerifier` adapters: `build_generic_oauth_provider` passes an optional
  `client_metadata_url` and otherwise lets the SDK choose CIMD or DCR on its own,
  while `build_entra_oauth_provider` pre-seeds a pre-registered `client_id` into
  storage *before* `OAuthClientProvider` runs its "do I have client info?" check
  - the flow's Step 4 (register) is then skipped entirely, since Entra exposes
    neither a `registration_endpoint` nor CIMD support to hit instead.
- Issuer binding is security-significant for pre-registered credentials, but the
  generic SDK behavior is intentionally more flexible than this Entra adapter needs:
  when the AS changes it may discard bound client information and continue into
  CIMD/DCR. The Entra adapter therefore adds an exact tenant-AS pin around the SDK's
  public auth-flow interface and fails before any request can be sent to an unexpected
  AS or registration endpoint. See ADR-0004.
- The demo entrypoint (`entrypoints/demo_client.py`) wires these adapters
  together and calls the companion server's `whoami` and `health` tools, so the
  full loop - authenticate, connect, call a tool - is one command someone can
  run and read end to end, not just unit-tested pieces.

## Consequences

- Adding a third authorization-server shape means writing one more
  `build_*_oauth_provider` factory function, not touching
  `entrypoints/demo_client.py` beyond its provider dispatch.
- PKCE, discovery, RFC 9207 validation, and generic registration-method selection
  remain the SDK's responsibility. The Entra adapter deliberately owns only the
  stricter public-client and authorization-server trust boundary on top: no secret,
  `token_endpoint_auth_method="none"`, and exact tenant endpoint pinning - exactly what
  `tests/unit/test_entra_client_auth.py` and
  `tests/unit/test_generic_oidc_client_auth.py` exist to catch.
- `FileTokenStorage` is a convenience for a single local user running a CLI, not
  a production secrets-management recommendation; a real deployment built from
  this template should swap it for an OS keyring or a secrets manager without
  touching anything outside `adapters/token_storage.py`.
