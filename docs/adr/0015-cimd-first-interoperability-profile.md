# ADR-0015: Make CIMD-first interoperability executable

- Status: Accepted
- Date: 2026-08-09

## Context

The generic OIDC client already passes an optional HTTPS `client_metadata_url` to the official MCP
Python SDK. The SDK uses that URL as the public `client_id` when authorization-server metadata
advertises `client_id_metadata_document_supported=true`, and falls back to Dynamic Client
Registration otherwise. Unit tests and documentation described this behavior, but the published
cross-repository contract and live E2E proved only DCR.

MCP 2026-07-28 deprecates DCR for new integrations and recommends Client ID Metadata Documents
when client and authorization server have no pre-existing relationship. Keeping DCR as the only
executable registration evidence would make the reference profile contradict its recommended
integration path.

## Decision

- Add `client-id-metadata-document` to the shared cross-repository positive-evidence set while
  retaining `dynamic-client-registration` as backwards-compatibility evidence.
- Extend the deterministic fake authorization server so a test can advertise CIMD support. In that
  mode it rejects DCR and accepts only the fixed HTTPS metadata-document URL as the client ID.
- Add a real OAuth/MCP E2E that configures that URL, completes PKCE and resource-bound token
  exchange, calls `whoami`, and proves there was no registration request or client secret.
- Keep a separate DCR fallback E2E. DCR remains supported but is not the recommended profile.
- Treat the remote metadata document as pre-validated fixture data in the fake authorization
  server. Fetching and validating that document is authorization-server behavior, not client or
  resource-server behavior, and adding outbound test network traffic would make this suite less
  hermetic without testing code owned by either repository.

## Consequences

- The preferred MCP 2026 client-registration path is now executable at the SDK support floor and
  across the real companion resource server.
- The E2E proves the client's selection and wire identity, but does not claim to validate an
  authorization server's CIMD SSRF, caching, document-validation, or trust-policy implementation.
- Both repositories must publish the identical updated pair contract. Merge the server contract
  first, then the client change whose E2E reads `server/main`.
- Existing DCR deployments remain covered and unchanged.
