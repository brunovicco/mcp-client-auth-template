# ADR-0009: Fail fast with a network-silent production configuration preflight

## Status

Accepted.

## Context

The interactive client owns several trust-sensitive values before OAuth begins: the MCP resource
URL, loopback redirect listener, Entra identifiers, optional Client ID Metadata URL, discovery
limits, and the local token-store path. Relying on the first browser redirect or HTTP request to
surface a malformed deployment makes operational failures late and can expose configuration values
through exception text.

A production preflight must not turn authorization-server availability into a startup dependency.
It therefore must not perform DNS, HTTP, discovery, JWKS retrieval, browser launch, or token I/O.

## Decision

The client provides `python -m mcp_client_auth_template.entrypoints.preflight` and a JSON mode for
CI. `Settings` validates structural invariants shared by every environment. The preflight adds
production-only policy:

- the MCP resource uses HTTPS;
- the insecure-loopback escape is disabled;
- localhost and documentation placeholder hosts are rejected;
- zero UUID placeholders are rejected for Entra tenant/application identifiers;
- optional Client ID Metadata remains HTTPS and cannot use a placeholder host.

Failures emit only field locations and stable issue types. Configured URLs, identifiers, scopes,
credentials, tokens, and filesystem contents are never echoed.

## Consequences

Misconfiguration is detected before OAuth or network activity begins, and the same command can run
in CI/CD admission checks. IdP reachability is intentionally excluded; runtime discovery remains
responsible for validating the remote trust chain under the P1.1 egress policy.
