# ADR-0016: Make the modern request envelope an executable pair contract

- Status: Accepted
- Date: 2026-08-09

## Context

The MCP 2026-07-28 Streamable HTTP profile makes every request self-describing. The protocol
version appears in both the HTTP header and `params._meta`; `Mcp-Method` mirrors the JSON-RPC
method; and `Mcp-Name` mirrors the named target for calls such as `tools/call`. Modern HTTP is
sessionless and rejects header/envelope disagreement or unsupported versions with structured
JSON-RPC errors.

The client already uses the official MCP Python SDK v2 in automatic negotiation mode, so its
successful E2E calls depend on the SDK emitting a coherent modern envelope. The published pair
contract did not explicitly claim the routing headers, version rejection, or sessionless wire
behavior, and the live suite did not exercise their negative boundary.

## Decision

- Keep request stamping delegated to the official MCP SDK. Do not introduce a client-owned
  parallel header builder into production code.
- Extend the cross-repository E2E with direct boundary requests against the real companion server:
  a valid self-describing call carrying a legacy-looking session ID, mismatched `Mcp-Method` and
  `Mcp-Name`, and a coherent but unsupported protocol version.
- Require JSON-RPC `-32020` for routing-header/envelope disagreement and `-32022` with
  supported/requested version data for version negotiation failure.
- Add matching positive and negative evidence to the shared pair contract.

## Consequences

- A successful SDK-driven OAuth/MCP flow plus server-side mismatch enforcement proves both halves
  of the pair contract without coupling production code to private SDK internals.
- The modern client remains sessionless. A legacy-looking `Mcp-Session-Id` cannot create hidden
  identity or authorization state and is not returned by the server.
- The E2E remains deterministic and local; no external identity provider or network service is
  added.
- Merge the companion server contract before this client change because CI compares against
  `server/main`.
