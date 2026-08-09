# Privacy and data handling

This service persists more than the server template does: unlike the resource server (which
holds a bearer token only in process memory for the duration of one request), this client
optionally writes access and refresh tokens to a local file
(`adapters/token_storage.py:FileTokenStorage`) so a person running the demo is not sent through
the browser consent screen on every run. That file - not just process memory - is this
document's main concern. Non-interactive mode additionally receives a pre-registered client secret
from the process environment, uses it only for token-endpoint authentication, and never writes it
or its acquired access tokens to that file.

## Data inventory

| Data category | Source | Purpose | Legal/contractual basis | Destination | Retention | Deletion method |
|---|---|---|---|---|---|---|
| OAuth access token, refresh token, and registered client information (`client_id`, issuer binding, and any registration fields returned by a generic DCR authorization server) | The configured authorization server, via the authorization code + PKCE exchange | Authenticate subsequent MCP tool calls without re-prompting the user every run | Necessary to provide the requested service (RFC 6749/8707 client authentication) | Local file at `MCP_CLIENT_TOKEN_STORAGE_PATH` (default `~/.mcp-client-auth-template/tokens.json`), inside a private POSIX directory | Until the file is deleted or the token is revoked at the authorization server | Delete the file, or unset `MCP_CLIENT_TOKEN_STORAGE_PATH` to use `InMemoryTokenStorage` instead, which retains nothing past the process |
| Pre-registered client ID, client secret, and client-credentials access token | Deployment secret manager/environment and configured authorization server | Authenticate an unattended service and call the MCP resource | Necessary to provide the requested service (OAuth Client Credentials extension) | Process memory; the secret is sent only to the discovered token endpoint over the hardened HTTPS path | Process lifetime (access tokens may expire sooner) | Stop the process and rotate/revoke the credential at the authorization server |

## Controls

- Data minimization: only the token response and client registration fields the SDK's
  `OAuthToken`/`OAuthClientInformationFull` models define are stored - no user profile data is
  requested or persisted beyond what the `scope` configured in `.env` grants.
- Client-credentials isolation: `Settings` stores the secret as `SecretStr`; machine mode forces
  `InMemoryTokenStorage`, and the SDK's fixed confidential-client record is not delegated to file
  storage. Configuration errors identify missing field names only, never values.
- Access control: on POSIX, `FileTokenStorage` requires the containing directory to be owned by the
  current uid with mode `0700`, and the JSON file to be a single-link regular file owned by the
  current uid with mode `0600`. Symbolic-link path components, a symbolic-link token file, hard
  links, unexpected ownership, and permissive modes fail closed. Windows must use in-memory
  storage or an OS-native keyring/credential adapter.
- Write integrity: persistent updates are written to a unique same-directory temporary file,
  `fsync`ed, atomically installed with `os.replace`, and followed by a directory `fsync`; a failed
  replacement leaves the previous committed file intact. Reads are capped at 1 MiB and malformed,
  empty, or non-object JSON fails as corruption rather than silently resetting authentication.
- Encryption in transit: external OAuth discovery, registration, and token endpoints are HTTPS by
  default and pass through the SSRF/DNS-pinning boundary in ADR-0006. The native callback itself is
  plain HTTP only on a literal loopback IP; the hardened listener can reject unrelated or malformed
  local requests while it waits for the bound authorization response.
- Encryption at rest: none - `FileTokenStorage` is still plaintext JSON. Filesystem controls reduce
  accidental/local cross-user disclosure and corruption but are not a substitute for encryption.
  Higher-assurance deployments should replace it with an OS keyring/keychain or secrets manager.
- Masking/tokenization: not applicable - tokens are opaque credentials, not maskable personal
  data.
- Non-production data strategy: tests never call a real authorization server; they use
  `InMemoryTokenStorage` or a `tmp_path`-scoped `FileTokenStorage`, and the loopback-server tests
  drive a real local socket with synthetic `code`/`state` values only.
- Logging and tracing restrictions: nothing in `adapters/` or `entrypoints/` logs a token,
  `client_secret`, or authorization code - `entrypoints/demo_client.py` logs only the `whoami`
  tool's own response (the caller's own identity) and the `health` check. OpenTelemetry spans
  produced by `a2a-otel-kit`'s `Observability`/`TracingAsyncTransport` are metadata-only: custom
  attributes pass through `a2a_otel_kit.domain.attributes.sanitize_attributes`'s bounded allowlist
  and must never contain prompts, responses, credentials, authorization headers, personal data,
  arbitrary URLs, tool output, or production payloads; `TracingAsyncTransport` never reads a
  request or response body. See `docs/OBSERVABILITY.md` for the full allowlist and configuration
  reference. W3C baggage is not propagated by default.
- Data-subject deletion/anonymization: delete `MCP_CLIENT_TOKEN_STORAGE_PATH`'s file, or revoke
  the token/client registration at the authorization server directly.
- External processors: the configured authorization server is the only external system this
  client calls for authentication; the configured MCP resource server is the only one it calls
  afterward. If `A2A_OTEL_ENABLED=true` is configured, the configured `A2A_OTEL_OTLP_ENDPOINT`
  becomes an additional external processor - keep it metadata-only per the policy above.
- Incident-response owner: set per deployment - this template does not prescribe one.

## Prohibited logging

Secrets, authentication headers, personal identifiers, full financial identifiers, complete request/response payloads, prompts, and model outputs containing sensitive data.
