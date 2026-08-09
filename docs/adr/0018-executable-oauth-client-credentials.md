# ADR-0018: Add a generic-OIDC OAuth Client Credentials reference profile

- Status: Accepted
- Date: 2026-08-09
- Supersedes: ADR-0004 decision 6 for generic OIDC only

## Context

The interactive authorization-code path cannot serve unattended workers, scheduled jobs, or
service-to-service integrations. MCP now publishes the draft
`io.modelcontextprotocol/oauth-client-credentials` extension, and MCP Python SDK 2.0.0 includes
`ClientCredentialsOAuthProvider`. The extension requires pre-registered credentials and does not
use Dynamic Client Registration.

Microsoft Entra and generic OIDC do not expose identical application-authorization contracts.
Entra client credentials request `{resource}/.default` and carry application permissions in
`roles`; the generic reference server authorizes OAuth scopes and deliberately does not infer an
Entra-style application principal from provider-specific claims.

## Decision

- Add `MCP_CLIENT_AUTH_MODE=client_credentials` for `auth_provider=generic` only.
- Use the official SDK provider with a pre-registered client ID, a process-injected secret, and
  `client_secret_basic`; advertise the extension on every MCP request.
- Skip browser, loopback callback, CIMD, and DCR in this mode. Keep acquired tokens in memory and
  never persist the fixed client credential.
- Retain Protected Resource Metadata, authorization-server discovery, RFC 8707 resource binding,
  bearer verification, and the SDK's bounded pre-dispatch scope step-up.
- Prove the profile with the companion server and fake OIDC AS. Do not claim live Entra client
  credentials interoperability in this increment.

## Consequences

- One demo entrypoint now covers interactive users and unattended generic-OIDC clients without
  duplicating the SDK OAuth state machine.
- Shared secrets remain a bootstrap option, not a preferred long-term credential. Deployments must
  inject and rotate them through a secret manager; JWT client assertions remain a future hardening
  increment.
- A generic client-credentials token proves a non-interactive machine client ID, but not an Entra
  application principal or app role.
- Merge the companion server change before this client change because client-owned E2E expects the
  server capability advertisement and compares the shared contract.
