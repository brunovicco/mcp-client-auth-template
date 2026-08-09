# ADR-0014: Versioned cross-repository compatibility contract

- Status: Accepted
- Date: 2026-08-09

## Context

Local Python, MCP SDK, authorization-provider, and transport matrices do not prove that the
client and server templates remain interoperable as a pair. The existing E2E suite already
exercises a real local generic-OIDC authorization flow against the companion server, but the
expected protocol revision and security evidence were implicit in test code.

An interoperability claim also needs a deterministic way to fail before runtime tests when the
two repositories silently diverge on the contract they intend to support.

## Decision

Both repositories publish the same versioned machine-readable document at
`compatibility/cross-repository.json`. The contract pins:

- MCP protocol revision `2026-07-28`;
- Streamable HTTP;
- the generic OIDC OAuth 2.1 E2E profile;
- the `mcp:tools:call` scope;
- positive evidence for protected-resource metadata, authorization-server discovery, DCR,
  PKCE S256, RFC 9207 authorization-response issuer validation, resource indicators, bearer
  access tokens, `server/discover`, and `tools/call`;
- negative evidence for authorization-server binding, invalid token claims, insufficient scope,
  and authorization-response issuer mismatch.

`scripts/cross_repository_contract.py` validates the local document. When passed `--peer-root`,
it also canonicalizes and compares the companion repository's contract before any E2E request.

The client owns the live cross-repository CI because it initiates OAuth and MCP calls. Its E2E
workflow checks out `mcp-server-auth-template` from `main`, requires the peer contract to match,
and then runs the existing OAuth/MCP suite. The server validates its local contract through its
normal unit and compatibility test matrices.

Dynamic Client Registration remains part of this reference E2E profile because the generic test
authorization server exercises that path. This does not make DCR the preferred registration
mechanism for the 2026-07-28 protocol revision; richer client-metadata support belongs to the
next protocol-advanced phase.

## Consequences

- Protocol and security claims become reviewable data instead of prose-only expectations.
- Client/server contract drift fails before the integration flow starts.
- The E2E job records the exact client and server commit SHAs used as compatibility evidence.
- The server PR must merge before the client PR so `server/main` publishes the peer contract when
  the client cross-repository check runs.
- The existing E2E behavior remains unchanged; this phase wraps it in an explicit compatibility
  boundary rather than rewriting runtime authentication.
