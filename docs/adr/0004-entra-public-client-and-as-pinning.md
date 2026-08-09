# ADR-0004: Entra interactive flow is a public client with exact AS pinning

- Status: Accepted
- Date: 2026-08-08

> Decision 6 is superseded by ADR-0018 for the generic-OIDC client-credentials mode. Entra remains
> an interactive public-client profile in this repository.

## Context

The demo is a native/CLI application using a system browser and loopback redirect.
RFC 8252 treats this deployment model as a public client because software installed
on an end-user machine cannot reliably keep a shared client secret confidential.
Microsoft Entra's authorization-code flow supports PKCE for desktop/native apps and
requires a client secret only for confidential web applications.

The MCP Python SDK v2 correctly binds stored credentials to an issuer. Its generic
behavior is intentionally portable, however: if Protected Resource Metadata points
at a different authorization server, the SDK may discard the bound client record and
continue with CIMD or Dynamic Client Registration. That is useful for generic MCP
clients but is the wrong trust model for an Entra application that was explicitly
pre-registered in one tenant and for which Entra offers no DCR/CIMD path.

## Decision

1. The interactive Entra adapter is public-client-only:
   - no `client_secret` setting or factory argument exists;
   - stored client information always has `client_secret=None`;
   - `token_endpoint_auth_method` is explicitly `none`;
   - authorization code + SDK-generated PKCE S256 remains the only interactive grant.
2. Existing token storage records that still contain a client secret or confidential
   token-endpoint auth method are replaced with the canonical public-client record.
3. The expected authorization server is exactly
   `https://login.microsoftonline.com/{tenant_id}/v2.0`.
4. A thin `PinnedEntraOAuthClientProvider` wraps the SDK's public `async_auth_flow`
   generator and validates every SDK-generated network request before it is yielded
   to `httpx2`:
   - the original MCP request must remain on the configured resource-server origin;
   - PRM discovery must remain on that same resource origin;
   - AS metadata and token requests must remain on the pinned Entra origin and known
     tenant-specific endpoint paths;
   - registration requests are not permitted.
5. The browser authorization URL is independently checked against the exact tenant's
   `/oauth2/v2.0/authorize` endpoint before the system browser is opened.
6. Machine-to-machine authentication is intentionally not added to this entrypoint.
   A future client-credentials implementation must use a separate non-interactive
   provider/entrypoint and its own credential-storage policy.

## Consequences

- A compromised or misconfigured MCP PRM document cannot redirect this Entra client
  into registration or token exchange with another authorization server.
- Legacy confidential-client configuration is removed rather than silently retained.
- The generic OIDC adapter keeps the SDK's normal CIMD/DCR portability; the stricter
  pin is provider-specific instead of weakening the reusable generic path.
- This guard is not a replacement for the broader discovery SSRF controls planned in
  P1.1e. DNS rebinding, private-address blocking, redirect hop validation, response
  size, and timeout policies remain a separate hardening step.
