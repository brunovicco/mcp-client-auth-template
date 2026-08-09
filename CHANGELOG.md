# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is
pre-1.0 and remains a reference template under active development.

## [Unreleased]

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

[Unreleased]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/brunovicco/mcp-client-auth-template/releases/tag/v0.2.0
