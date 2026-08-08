# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project does not yet follow semantic versioning (pre-1.0, template in active development).

## [Unreleased]

### Added

- OAuth 2.1 native-client authentication via the `mcp` SDK's `OAuthClientProvider`, with two
  swappable provider adapters: Microsoft Entra ID (pre-registered client, no DCR/CIMD) and a
  generic standards-compliant OIDC authorization server (Auth0, Keycloak, WorkOS AuthKit, ...)
  with optional CIMD and automatic DCR fallback.
- `FileTokenStorage` (permissions-locked local JSON) and `InMemoryTokenStorage` adapters
  implementing the SDK's `TokenStorage` protocol.
- RFC 8252 one-shot loopback callback server for receiving the authorization redirect.
- System-browser adapter for opening the authorization URL.
- `demo_client` entrypoint: wires storage, browser redirect, and the loopback callback into a
  streamable-HTTP MCP session against the companion
  [`mcp-server-auth-template`](https://github.com/brunovicco/mcp-server-auth-template), calling
  its `whoami` and `health` tools.
- Structured logging (structlog), optional vendor-neutral OpenTelemetry tracing, and optional
  Langfuse LLM tracing, both network-silent unless explicitly configured.
- Offline unit tests for both provider adapters (fake in-memory token store, no network, no real
  IdP) and a project-owned quality gate (`scripts/quality_gate.py`) covering lint, format,
  typing, tests, security, architecture, MCP config, and governance checks.
