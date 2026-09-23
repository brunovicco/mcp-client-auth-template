# ADR-0024: Pre-provisioned machine credentials are issuer-bound

- Status: Accepted
- Date: 2026-09-22
- Amends: ADR-0018

## Context

In the OAuth Client Credentials profile (ADR-0018) the client holds a credential that was
provisioned by one specific authorization server: a client ID plus either a shared secret or,
since ADR-0025, a private key used to sign client assertions. The client does not learn which
authorization server to use from its configuration. It learns it from the MCP server's
Protected Resource Metadata (PRM).

PRM is controlled by the resource server. A compromised, misconfigured, or impersonated MCP
server can therefore advertise an authorization server of its choice. Before MCP SDK 2.2,
`ClientCredentialsOAuthProvider` then fetched that server's metadata and sent the pre-provisioned
credential to its token endpoint. That turns the MCP server into an oracle that decides where the
machine credential goes. This is the same credential-forwarding class that SEP-2352 closes for
registered interactive clients.

MCP SDK 2.2 adds `issuer=` to `ClientCredentialsOAuthProvider` and `PrivateKeyJWTOAuthProvider`,
and deprecates omitting it (`MCPDeprecationWarning`, required in MCP 3). When `issuer` is set:

- the SDK prefers the matching authorization server when PRM advertises several;
- it builds a token request only from authorization-server metadata whose `issuer` matches the
  configured value;
- for `private_key_jwt`, it mints an assertion only after that check passes;
- on any mismatch it raises `OAuthFlowError` and drops the held metadata and tokens.

## Decision

1. `MCP_CLIENT_CLIENT_CREDENTIALS_ISSUER` (`client_credentials_issuer`) is **mandatory** whenever
   `auth_mode=client_credentials`. It is rejected in interactive mode so it cannot give a false
   sense of pinning there.
2. The configured value gets only minimal, fail-closed structural validation. It must be:
   - non-empty and trimmed, with no control characters;
   - an absolute URL with a host;
   - free of userinfo, query, and fragment;
   - HTTPS, except HTTP on a loopback host with the explicit `oauth_allow_insecure_loopback`
     development opt-in.

   The production preflight additionally requires HTTPS and a non-placeholder host.
3. The value is kept exactly as configured and passed unchanged to the SDK's `issuer=`. The
   client does **not** canonicalize issuers, does not reimplement `issuers_match`, and adds no
   parallel binding layer. Binding the configured issuer to the discovered metadata is the MCP
   SDK's job.
4. The binding is proven over the real SDK flow and the real wire. Local fake servers journal
   every request, and tests show that an unexpected authorization server never receives a
   client ID in an authenticated request, a secret, a client assertion, or a bearer token.

## Consequences

- A malicious or misconfigured PRM can at most make the client fetch the unexpected server's
  public metadata. That request is unauthenticated. The flow then aborts before any credential
  leaves the process.
- Operators must configure one more value. A wrong issuer fails closed at the first token
  request, with an `OAuthFlowError` that names both issuers and no credential material.
- Issuer strings must match the authorization server's metadata the way the SDK compares them:
  simple string comparison, except that a root issuer with and without its trailing slash counts
  as the same. Trailing slashes on paths, case, and default ports are not normalized for the
  operator.
- The client-credentials path no longer emits the SDK deprecation, so the test suite promotes
  `mcp.MCPDeprecationWarning` to an error. MCP 3's required-issuer change is already absorbed.
