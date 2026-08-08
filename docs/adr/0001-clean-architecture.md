# ADR-0001: Adopt Clean Architecture dependency boundaries

- Status: Accepted
- Date: 2026-08-08

## Context

The service requires business rules to remain independent from web frameworks, persistence, messaging, and external SDKs.

## Decision

Use the dependency direction documented in `docs/ARCHITECTURE.md` and enforce it through package structure, review, tests, and import-contract tooling when introduced.

## Consequences

- Domain code remains independently testable.
- Boundary translation is explicit.
- Small CRUD features should not receive unnecessary abstraction.
- More mapping code is accepted where it protects domain semantics.

## Applied to this template

`domain/` and `application/` are intentionally empty scaffolding here, not an oversight: this
repository's only use case — authenticate, then call MCP tools — is already owned end to end by
the `mcp` SDK's `OAuthClientProvider` (see `docs/adr/0002-oauth21-native-client.md`), so there is
no business rule or application-layer port left for this template to define. The boundary still
does real work — `adapters/` translates the SDK's I/O ports (`TokenStorage`, browser redirect,
loopback callback) and `entrypoints/` wires and validates configuration — it just has nothing to
protect on the domain side yet. A concrete client built from this template that grows real
business rules (e.g., authorization decisions beyond what the AS already encodes in the token)
would add them to `domain/`/`application/` without touching `adapters/`, which is the boundary
this ADR exists to keep enforceable via `scripts/validate_architecture.py`.
