# Privacy and data handling

This service persists more than the server template does: unlike the resource server (which
holds a bearer token only in process memory for the duration of one request), this client
optionally writes access and refresh tokens to a local file
(`adapters/token_storage.py:FileTokenStorage`) so a person running the demo is not sent through
the browser consent screen on every run. That file - not just process memory - is this
document's main concern.

## Data inventory

| Data category | Source | Purpose | Legal/contractual basis | Destination | Retention | Deletion method |
|---|---|---|---|---|---|---|
| OAuth access token, refresh token, and registered client credentials (`client_id`, and a `client_secret` only for a confidential Entra registration) | The configured authorization server, via the authorization code + PKCE exchange | Authenticate subsequent MCP tool calls without re-prompting the user every run | Necessary to provide the requested service (RFC 6749/8707 client authentication) | Local file at `MCP_CLIENT_TOKEN_STORAGE_PATH` (default `~/.mcp-client-auth-template/tokens.json`), owner-only permissions | Until the file is deleted or the token is revoked at the authorization server | Delete the file, or unset `MCP_CLIENT_TOKEN_STORAGE_PATH` to use `InMemoryTokenStorage` instead, which retains nothing past the process |

## Controls

- Data minimization: only the token response and client registration fields the SDK's
  `OAuthToken`/`OAuthClientInformationFull` models define are stored - no user profile data is
  requested or persisted beyond what the `scope` configured in `.env` grants.
- Access control: `FileTokenStorage` writes with owner-only permissions (`0600`) immediately
  after every write; there is no shared or multi-user storage mode.
- Encryption in transit: all discovery, registration, and token requests go over HTTPS to the
  authorization server; the loopback callback listener itself is plain HTTP, but it only ever
  talks to `127.0.0.1` and only ever receives one request.
- Encryption at rest: none - `FileTokenStorage` is a permissions-restricted plaintext JSON file,
  explicitly documented in `docs/adr/0002-oauth21-native-client.md` as a demo convenience, not a
  production secrets-management recommendation. A real deployment should swap it for an OS
  keyring or a secrets manager.
- Masking/tokenization: not applicable - tokens are opaque credentials, not maskable personal
  data.
- Non-production data strategy: tests never call a real authorization server; they use
  `InMemoryTokenStorage` or a `tmp_path`-scoped `FileTokenStorage`, and the loopback-server tests
  drive a real local socket with synthetic `code`/`state` values only.
- Logging and tracing restrictions: nothing in `adapters/` or `entrypoints/` logs a token,
  `client_secret`, or authorization code - `entrypoints/demo_client.py` logs only the `whoami`
  tool's own response (the caller's own identity) and the `health` check. Document any enabled
  tracing backend, content-capture approval, redaction, retention, and access policy before
  enabling content-bearing tracing. Generic OpenTelemetry spans are metadata-only: custom
  attributes pass through a bounded allowlist and must never contain prompts, responses,
  credentials, authorization headers, personal data, arbitrary URLs, tool output, or production
  payloads. The public tracing wrappers enforce this policy for span and event attributes,
  operation names, status descriptions, and exception details. W3C baggage is not propagated by
  default.
- Data-subject deletion/anonymization: delete `MCP_CLIENT_TOKEN_STORAGE_PATH`'s file, or revoke
  the token/client registration at the authorization server directly.
- External processors: the configured authorization server is the only external system this
  client calls for authentication; the configured MCP resource server is the only one it calls
  afterward. If the optional OpenTelemetry or Langfuse tracing extras are enabled, their
  configured OTLP/Langfuse endpoint becomes an additional external processor - keep it
  metadata-only per the policy above.
- Incident-response owner: set per deployment - this template does not prescribe one.

## Prohibited logging

Secrets, authentication headers, personal identifiers, full financial identifiers, complete request/response payloads, prompts, and model outputs containing sensitive data.
