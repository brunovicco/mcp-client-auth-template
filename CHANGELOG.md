# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is
pre-1.0 and remains a reference template under active development.

## [Unreleased]

## [0.4.0] - 2026-08-09

### Changed

- Public package version is now `0.4.0`.
- Upgraded to `a2a-otel-kit[mcp]>=0.6,<0.7` and its native MCP SDK 2.x/HTTPX2 observability
  boundary.
- Removed the direct plain-HTTPX dependency and the nominal cross-package casts that were needed
  by the previous adapter.
- Reworked the English and Brazilian Portuguese READMEs around adoption, architecture, security,
  engineering evidence, operations, and explicit production boundaries.
- Excluded local `.claude/` state from Docker build contexts.

### Security

- Preserved metadata-only W3C propagation, network-silent defaults, and the existing rule that
  tracing never captures credentials, MCP payloads, request/response bodies, arbitrary headers,
  URLs, baggage, or exception text.

## [0.3.0] - 2026-08-09

### Added

- Non-interactive OAuth Client Credentials mode for generic OIDC authorization servers.
- MCP OAuth Client Credentials extension advertisement.
- Client ID Metadata Document-first interactive OAuth interoperability.
- MCP `2026-07-28` self-describing request envelopes.
- Sessionless Streamable HTTP behavior and protocol-version negotiation.
- Runtime scope step-up with scope-union reauthorization.
- Positive and fail-closed cross-repository E2E scenarios.
- ADRs covering CIMD, modern request envelopes, runtime scope step-up, and Client Credentials.

### Changed

- Public package version is now `0.3.0`.
- The cross-repository contract now covers interactive and machine-to-machine authentication.
- Machine mode bypasses browser callbacks, CIMD, Dynamic Client Registration, and persistent token
  storage.
- Dynamic Client Registration remains only as a backwards-compatible generic OIDC path.

### Security

- Client secrets are represented with `SecretStr` and remain memory-only.
- Machine-mode tokens and credentials are never written to persistent token storage.
- Invalid client credentials fail closed without exposing secret values.
- Authorization-server binding and exact MCP resource bearer boundaries remain enforced.
- Generic client credentials remain distinct from Microsoft Entra app-only authorization.

## [0.2.0] - 2026-08-09

### Added

- Native/public OAuth 2.1 client authentication for Microsoft Entra ID and generic OIDC.
- Authorization Code + PKCE with a bounded RFC 8252 loopback callback listener and system-browser
  integration.
- In-memory and hardened file-backed token storage.
- RFC 9207 authorization-response issuer validation and authorization-server credential binding.
- Production configuration preflight, explicit operational timeout/cancellation budgets, and
  stable CLI failure categories.
- Executable compatibility matrices for Python 3.13/3.14, MCP SDK `2.0.0`/latest compatible 2.x,
  Entra/generic OIDC, production HTTPS, and IPv4/IPv6 loopback development profiles.
- A versioned cross-repository compatibility contract plus a real local OAuth/MCP E2E flow against
  `mcp-server-auth-template`.

### Changed

- Public package version is now `0.2.0`.
- Supported Python range is `>=3.13,<3.15`; the CI matrix exercises Python 3.13 and 3.14.
- Supported MCP Python SDK range is `>=2.0,<3`, with `2.0.0` as the tested support floor.
- Generic OIDC prefers Client ID Metadata Documents where advertised; Dynamic Client Registration
  remains only as a backwards-compatible reference path.
- Tool calls have an explicit deadline and deterministic cleanup behavior.

### Security

- OAuth discovery rejects unsafe schemes, redirects, compression, oversized responses,
  private/reserved destinations, mixed DNS answers, and unsafe endpoint changes.
- Bearer credentials are forwarded only to the exact configured MCP resource endpoint.
- File token storage enforces owner/permission, symlink/hardlink, size, corruption, atomic-write,
  and durability checks.
- HTTP loopback development requires explicit opt-in; production remains HTTPS-only.

[Unreleased]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/brunovicco/mcp-client-auth-template/releases/tag/v0.3.0
[0.2.0]: https://github.com/brunovicco/mcp-client-auth-template/releases/tag/v0.2.0
