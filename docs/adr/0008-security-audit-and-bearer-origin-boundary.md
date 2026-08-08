# ADR-0008: Audit blocked credential egress and bind bearer tokens to the MCP resource

- Status: Accepted
- Date: 2026-08-08

## Context

MCP 2026-07-28 requires clients to send access tokens only to the MCP server for which those
tokens were issued. OAuth discovery introduces several additional network destinations—protected
resource metadata, authorization-server metadata, registration, token, and authorization
endpoints—but an MCP bearer credential must not follow those destinations.

P1.1e already validates and DNS-pins every outbound OAuth/MCP request and rejects cross-origin
redirects. The credential boundary should be independently explicit so a future SDK or redirect
behavior change cannot cause an already-acquired MCP access token to be replayed to another origin
or even to another path on the resource origin.

## Decision

1. `PinnedDnsAsyncTransport` detects Bearer authorization before DNS or network I/O.
2. Bearer credentials are accepted only for the exact configured MCP data-plane endpoint
   (`resource origin + /mcp`). Same-origin metadata/token paths are not eligible.
3. A blocked bearer attempt fails closed with `OAuthNetworkSecurityError` before any child
   transport is selected or invoked.
4. The client emits a minimized `security_audit` record containing only action, outcome, reason,
   and target kind. It never records the token, Authorization header, URL query, state, code, or
   refresh token.
5. Non-Bearer Authorization schemes are not globally prohibited by this guard because an OAuth
   authorization server may legitimately require its own client-authentication mechanism. This
   native public-client template does not currently use such a mechanism.

## Consequences

- DNS/redirect SSRF controls and credential audience/origin controls are independent defenses.
- A compromised or malicious metadata document cannot receive the MCP bearer even if its URL
  otherwise passes the network policy.
- The guard does not replace server-side audience validation; both sides enforce their own trust
  boundary.
