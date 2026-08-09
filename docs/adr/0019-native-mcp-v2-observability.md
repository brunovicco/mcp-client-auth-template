# ADR-0019: Adopt the native MCP SDK 2.x observability boundary

- Status: Accepted
- Date: 2026-08-09
- Supersedes: ADR-0003's HTTPX compatibility workaround and version constraint

## Context

The original `a2a-otel-kit` integration used version 0.4 with MCP SDK 2.x. That kit release still
typed its client adapter against plain HTTPX and declared an MCP 1.x extra, while the SDK 2.x used
its `httpx2` transport fork. Runtime duck typing made the pair work, but the client needed a direct
plain-HTTPX dependency and two explicit casts at the integration boundary.

`a2a-otel-kit` 0.6.0 now supports `mcp>=2.0,<3` and implements `TracingAsyncTransport` directly on
`httpx2.AsyncBaseTransport`. The companion server also uses the same released kit version at its
inbound ASGI boundary.

## Decision

- Depend on `a2a-otel-kit[mcp]>=0.6,<0.7` as a core runtime dependency.
- Pass the DNS-pinned `httpx2.AsyncBaseTransport` directly to `TracingAsyncTransport.wrap(...)`.
- Pass the returned `httpx2.AsyncBaseTransport` directly to `httpx2.AsyncClient`.
- Remove the direct plain-HTTPX dependency and both nominal compatibility casts.
- Keep `Observability` lifecycle ownership, opt-in export, metadata-only spans, and security
  transport ordering unchanged.

## Consequences

- The client, MCP SDK, and observability adapter share one native HTTPX2 type contract.
- The supported kit range is aligned with the companion server and covered by the existing Python
  and MCP SDK compatibility matrices.
- W3C trace context can flow across the client/server pair without introducing a second HTTP stack
  or weakening DNS pinning, bearer-origin restrictions, or content-exclusion rules.
