# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project is
pre-1.0 and remains a reference template under active development.

## [Unreleased]

## [0.6.0] - 2026-08-10

### Added

- Added P1.7d public-repository polish: concise EN/PT-BR landing pages, executable reference-demo CI, real visual evidence, and repository-hygiene regression coverage.
- Removed checked-in coding-agent/Codex scaffolding and Codex-only MCP configuration policy so the public tree contains project-owned runtime, tests, CI and documentation.
- Added P1.7c, an optional local observability overlay with `a2a-otel-kit`,
  OpenTelemetry Collector, Tempo, Grafana, positive Collector receipt verification,
  MCP client/server trace continuity, Tempo retrieval, Grafana datasource validation,
  and metadata-only privacy assertions.
- Added P1.7b, a hardened Docker Compose reference demo that consumes the published Server
  `v0.5.0` by immutable digest and preserves the loopback-only development boundary.
- Added P1.7a, a one-command headless reference demo that starts the deterministic local OIDC
  provider and real companion server, proves CIMD-first Authorization Code + PKCE,
  authenticated MCP tool calls, bounded runtime scope step-up, wrong-audience rejection, and
  stateless MCP `2026-07-28` behavior without external credentials or a browser.

### Changed

- Public package version is now `0.6.0`.
- Secure container publication now produces one OCI index for `linux/amd64` and `linux/arm64`, with immutable architecture-specific version/commit aliases.
- GitHub Releases use the curated `.github/release-notes/v0.6.0.md` file.

### Security

- AMD64 and ARM64 production images are independently inventoried, scanned, and policy-approved before GHCR authentication.
- The exact scanned local platform images are pushed; no post-scan rebuild is used for publication.
- `image-platforms.json` binds the final OCI index digest to the exact scanned platform digests, and the release validator rejects platform drift.
- The final index receives build provenance and each platform manifest receives its own CycloneDX SBOM attestation.

## [0.5.0] - 2026-08-09

### Added

- Added an executable supply-chain trust baseline with SHA-pinned GitHub Actions, explicit
  least-privilege workflow permissions, controlled Dependabot updates, and dependency/license
  review.
- Added CycloneDX source and production-image inventories, checksum-verified Syft/Grype tooling,
  complete vulnerability evidence, and a fail-closed policy for actionable findings with narrow,
  expiring exceptions.
- Added allowlisted, byte-reproducible wheel and sdist builds with exact build constraints,
  SHA-256 manifests, and GitHub build-provenance attestations.
- Added tag-gated GitHub Release publication with reproducible Python packages, complete checksum
  coverage, CycloneDX inventories, vulnerability evidence, and a machine-readable release manifest.
- Added GHCR publication for the policy-approved production image, identified by immutable digest
  and accompanied by build-provenance and SBOM attestations.

### Changed

- Public package version is now `0.5.0`.
- Release publication is split across isolated artifact-build, container-publication, and GitHub
  Release jobs; PyPI publication remains out of scope.
- Existing GHCR version and commit tags are never overwritten; a partial publication requires a
  new version rather than reusing a partially published version.

### Fixed

- Made the checksum-corruption regression test deterministic so release-integrity failures are
  exercised reliably.
- Derive the OpenTelemetry service version from installed package metadata so released telemetry
  identifies `v0.5.0` correctly.

### Security

- GHCR authentication happens only after the vulnerability policy approves the locally built
  production image.
- Attestation, registry, and GitHub Release write authority are isolated into narrowly scoped jobs.
- Release builds fail closed on version/tag mismatch, non-reproducible artifacts, unexpected
  archive contents, unsafe paths, checksum drift, stale or expired vulnerability exceptions, and
  release-bundle inconsistencies.

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

[Unreleased]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/brunovicco/mcp-client-auth-template/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/brunovicco/mcp-client-auth-template/releases/tag/v0.3.0
[0.2.0]: https://github.com/brunovicco/mcp-client-auth-template/releases/tag/v0.2.0
