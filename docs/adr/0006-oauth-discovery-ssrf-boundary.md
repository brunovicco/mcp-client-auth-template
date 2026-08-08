# ADR-0006: Pin OAuth discovery egress to validated public IPs

- Status: Accepted
- Date: 2026-08-08

## Context

An MCP client does not choose every URL it contacts during OAuth. The resource server can
influence Protected Resource Metadata discovery, the PRM document supplies authorization-server
URLs, and authorization-server metadata supplies registration/token/authorization endpoints.
Those indirections are an SSRF boundary: a malicious or compromised server could otherwise point
the client at localhost, RFC1918 networks, link-local cloud metadata endpoints, reserved address
space, or a hostname that changes from a public to a private address between validation and use.

The MCP security guidance explicitly calls out direct internal-IP access, cloud metadata,
localhost, redirects and DNS rebinding. HTTPS URL validation by itself is not enough because a
perfectly valid HTTPS hostname can resolve to a non-public address.

## Decision

1. Every HTTP request made by the demo's shared `httpx2.AsyncClient` passes through
   `PinnedDnsAsyncTransport` below the existing tracing transport.
2. Production policy permits only HTTPS targets whose complete DNS answer set is globally
   routable. Any private, loopback, link-local, reserved, multicast, unspecified, or otherwise
   non-global address causes the request to fail closed. IPv4-mapped IPv6 is classified by its
   mapped IPv4 address.
3. Local development has one explicit escape hatch:
   `MCP_CLIENT_OAUTH_ALLOW_INSECURE_LOOPBACK=true`. It permits HTTP/HTTPS only when every resolved
   address is loopback. It never permits RFC1918/LAN or link-local targets.
4. DNS is resolved immediately before each actual request. The transport connects to the selected
   validated IP literal and preserves the original hostname in the HTTP `Host` header and
   HTTPX2's `sni_hostname` TLS extension. The lower transport therefore cannot perform a second DNS
   lookup between the policy check and TCP connect.
5. Each logical hostname has its own connection pool. Two hostnames that happen to resolve to the
   same IP cannot accidentally reuse a TLS connection established for the other hostname.
6. Redirects remain enabled for compatibility, but every redirect target is validated when the
   response is received and again when the next request is about to connect. HTTP redirects must
   remain on the same origin so token/DCR request bodies cannot be replayed to another host;
   `oauth_max_redirects` bounds the chain.
7. OAuth control-plane responses are bounded by `oauth_max_response_bytes`. The client requests
   identity encoding and rejects compressed control-plane responses, validates `Content-Length`
   when present, and also enforces the limit while streaming so chunked responses cannot bypass it.
8. DNS lookup time, OAuth HTTP connect/read/write/pool time, and the number of distinct outbound
   host pools are separately bounded. The MCP data plane keeps its longer SSE-friendly timeout.
9. Authorization URLs handed to the system browser receive the same URL/DNS allow/deny policy.
   Browser networking cannot be IP-pinned by this process; provider-specific trust controls such
   as the exact Entra issuer/endpoint pin from ADR-0004 remain authoritative there.

## Consequences

- PRM/OIDC metadata cannot turn this process into a network client for cloud metadata, local
  services, RFC1918 networks, link-local addresses, or other non-global destinations by default.
- DNS rebinding does not get a second resolver decision inside HTTPX2: the connect target is the
  IP that was just validated while TLS certificate verification still uses the intended hostname.
- Safe redirects can still work, but each hop crosses the same network boundary and the chain is
  bounded.
- The default settings are intentionally unsuitable for an HTTP localhost demo. `.env.example`
  opts into loopback HTTP explicitly so the exception is visible rather than implicit.
- Private enterprise MCP/authorization endpoints are blocked by this template's default policy.
  Deployments that intentionally require private egress should replace this adapter with an
  organization-controlled egress proxy/network policy rather than weakening address validation.
- This ADR does not replace P1.1f server-side OIDC/JWKS hardening or P1.1g token-storage hardening.
