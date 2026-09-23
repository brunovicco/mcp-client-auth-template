# Operations

## Configuration preflight

Run the network-silent preflight before starting a production client:

```bash
uv run python -m mcp_client_auth_template.entrypoints.preflight --json
```

Exit code `0` means local configuration passed. Exit code `1` means configuration is invalid. The
JSON result is deliberately sanitized and suitable for CI logs.

The preflight does **not** perform DNS, HTTP, OAuth discovery, token refresh, browser launch, or
authorization-server health checks. Production startup is therefore not coupled to temporary IdP
availability.

## Production baseline

Set `APP_ENV=production`, use a real HTTPS MCP resource URL, keep
`MCP_CLIENT_OAUTH_ALLOW_INSECURE_LOOPBACK=false`, use tenant-specific Entra UUIDs, and replace all
`.invalid`/example placeholders. The redirect listener remains an IP-literal loopback address even
in production for interactive mode.

Local file token storage remains single-user POSIX storage with the ownership and mode invariants
documented in ADR-0007. Use an OS keyring or secret-manager adapter instead when that filesystem
contract is not appropriate.

For `MCP_CLIENT_AUTH_MODE=client_credentials`, set `MCP_CLIENT_CLIENT_CREDENTIALS_ISSUER` to the
exact issuer identifier published by the authorization server that provisioned the credential. It
is mandatory. The MCP SDK compares it with discovered metadata as a simple string (a root issuer
with or without its trailing slash counts as the same), so copy it from the server's
`/.well-known/openid-configuration` or `oauth-authorization-server` document. If the MCP server
advertises a different authorization server, the client exits with the authentication category
before sending the credential anywhere. Production preflight requires HTTPS and rejects
placeholder hosts.

This mode does not open a redirect listener and overrides token-file storage with in-memory
storage; restarting the process discards its acquired access token.

### Machine client authentication

| `MCP_CLIENT_CLIENT_AUTH_METHOD` | Configure | Rotate |
| --- | --- | --- |
| `client_secret_basic` (default) | Inject `MCP_CLIENT_CLIENT_CREDENTIALS_SECRET` at process start from a secret manager | Replace the secret at the authorization server and redeploy |
| `private_key_jwt` | Register the public key with the authorization server; mount the private key and set `MCP_CLIENT_CLIENT_CREDENTIALS_PRIVATE_KEY_PATH` | Register the new public key, remount, restart, then retire the old key |

The two methods are mutually exclusive. `private_key_jwt` sends a 60-second, SDK-signed assertion
(`iss` = `sub` = client ID, `aud` = the authorization-server issuer, unique `jti`) and never a
shared secret. The SDK emits no `kid`/`x5t` header, so register a single key for the client, or use
an authorization server that tries each registered key. Entra certificate credentials are out of
scope for this generic-OIDC profile.

The private key is read once, at startup and by preflight, and must satisfy an SSH
StrictModes-style policy (ADR-0025):

- an absolute path with **no symbolic link** in any component;
- every directory owned by the current user or root, and not group- or world-writable (world-writable
  `/tmp` is therefore rejected);
- the key file is a regular, single-link file owned by the current user or root, with mode `0600`
  or `0400`, and at most 64 KiB;
- an unencrypted PEM RSA key of at least 2048 bits (signed RS256) or P-256 EC key (ES256).

A violation fails preflight with only `client_credentials_private_key_path:private_key_rejected`,
and fails the CLI with the configuration exit code. File contents are never echoed.

**Mounting the key.**

- **Docker / Compose secrets** are regular files under `/run/secrets` and work as long as the file
  mode is owner-only for the process user (for example `mode: 0400` with a matching `uid`).
- **Kubernetes Secret volumes** expose each key as a symlink into a `..data` directory, so the
  loader rejects them. Mounting the single key file with `subPath` presents a regular file and is
  accepted. **Trade-off:** Kubernetes never updates `subPath` mounts when the Secret changes, so key
  rotation requires restarting or redeploying the pod rather than happening automatically. Where
  automatic rotation matters, use a CSI secret-store driver that writes regular files, or an init
  container that copies the key into an `emptyDir` with `0400`. The symlink rule is intentionally
  not relaxed to accommodate projected volumes.

## Operational budgets and cancellation

The shared HTTP client has distinct connect, read, write, and pool timeouts. MCP tool calls also
have an outer application deadline (`MCP_CLIENT_TOOL_CALL_TIMEOUT_SECONDS`). When that deadline
expires, the in-flight SDK request is cancelled and the error is surfaced to the caller.

This template deliberately configures the underlying HTTP transport with `retries=0` and does not
automatically repeat a tool call after timeout or transport failure. Tool idempotency is a domain
property; a generic MCP client cannot assume a timed-out write had no side effect. Callers that add
retries later must do so per tool, with an explicit idempotency contract.

OAuth browser callback waiting is independently bounded by timeout and request-count settings.
Async HTTP/MCP resources are closed under a shielded shutdown deadline so process termination does
not wait forever on a stuck connection pool or transport close.

## Stable failure contract

The entrypoint converts expected operational failures into stable exit codes without
logging exception messages, response bodies, OAuth parameters, tokens, or tool result content:

| Exit | Category | Examples |
| ---: | --- | --- |
| `0` | success | Both demo tool calls completed. |
| `2` | configuration | Preflight rejected local settings, or the `private_key_jwt` key file violated the key policy. |
| `3` | authentication | OAuth flow, registration, or token exchange failed, including an authorization-server issuer mismatch or PRM `429`/`5xx`. |
| `4` | network | DNS/egress policy, HTTP transport, or broken stream failed. |
| `5` | timeout | An MCP tool exceeded its application deadline. |
| `6` | local storage | Token-store ownership, permissions, links, or JSON were unsafe. |
| `7` | tool | A tool returned `is_error=true`, or `whoami` was not in the principal's `tools/list` view. |
| `8` | MCP protocol | The peer returned an MCP protocol error. |
| `70` | internal | An unclassified software failure occurred. |
| `130` | interrupted | The operator interrupted the process. |

`mcp_client_failed` logs contain only `category`, `exit_code`, and `exception_type`. Server-controlled
MCP error messages and OAuth exception text are deliberately not copied into logs because those
strings can contain identifiers, response bodies, or other sensitive material.

A tool result with `is_error=true` is treated as a failed CLI run, but its content is not retained in
the failure object or logged. Successful `whoami` and `health` calls likewise emit only the tool name
and completion event. Applications that need to display business payloads should render them on an
explicit user-facing channel rather than placing them in operational logs.

In interactive mode, the headless OAuth fallback still prints the authorization URL to the terminal
because the operator must be able to copy it into a browser. That URL is no longer attached to the
structured `browser_open_failed` log event.
