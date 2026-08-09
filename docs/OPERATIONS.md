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

For `MCP_CLIENT_AUTH_MODE=client_credentials`, inject
`MCP_CLIENT_CLIENT_CREDENTIALS_SECRET` at process start from a secret manager and rotate it on a
deployment-specific schedule. This mode does not open a redirect listener and overrides token-file
storage with in-memory storage; restarting the process discards its acquired access token.


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
| `2` | configuration | Preflight rejected local settings. |
| `3` | authentication | OAuth flow, registration, or token exchange failed. |
| `4` | network | DNS/egress policy, HTTP transport, or broken stream failed. |
| `5` | timeout | An MCP tool exceeded its application deadline. |
| `6` | local storage | Token-store ownership, permissions, links, or JSON were unsafe. |
| `7` | tool | A tool returned `is_error=true`. |
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

In interactive mode, the headless OAuth fallback still prints the authorization URL to the terminal because the operator
must be able to copy it into a browser. That URL is no longer attached to the structured
`browser_open_failed` log event.
