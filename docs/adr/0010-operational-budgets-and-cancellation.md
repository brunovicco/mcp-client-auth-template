# ADR-0010: Bound MCP operations and never retry tools implicitly

## Status

Accepted.

## Context

A production client needs more than a single HTTP timeout. Connection establishment, response
reads, request writes, connection-pool acquisition, interactive OAuth callback waiting, MCP tool
execution, and resource shutdown can fail or stall independently.

Retrying a timed-out MCP tool generically is unsafe. A timeout says the client stopped waiting; it
does not prove that the server did not execute a side effect. A template that retries every tool can
therefore duplicate payments, writes, messages, or other future mutations.

## Decision

The client exposes distinct HTTP connect/read/write/pool budgets plus independent OAuth callback,
tool-call, and shutdown deadlines. Tool execution is wrapped in an AnyIO cancellation deadline.
The SDK and HTTP client are still closed after cancellation, under a shielded bounded shutdown
scope.

The DNS-pinned child HTTP transport is constructed with `retries=0`. No application-level retry is
performed for `tools/call`. A consuming application may add retries only when the individual tool
has an explicit idempotency contract or an application-level idempotency key.

## Consequences

Hung requests no longer wait indefinitely, timeout values are deployment-configurable, and cleanup
has a finite upper bound. Transient failures are surfaced rather than hidden by a duplicate
execution attempt. This is intentionally conservative for a reusable authentication template.
