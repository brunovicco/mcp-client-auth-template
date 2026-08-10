# P1.7a — One-command headless reference demo

P1.7a turns the existing cross-repository E2E evidence into a portfolio-facing executable demo.
It uses the real client and companion server, but replaces the external authorization server with
the deterministic localhost OIDC test double already used by the E2E suite.

## Run it

Clone the repositories side by side:

```text
workspace/
├── mcp-client-auth-template/
└── mcp-server-auth-template/
```

Then run exactly one command from the client repository:

```bash
./scripts/run_reference_demo.sh
```

For a different server checkout:

```bash
./scripts/run_reference_demo.sh --server-root /path/to/mcp-server-auth-template
```

For machine-readable output:

```bash
./scripts/run_reference_demo.sh --json
```

The wrapper syncs the locked client environment, installs the local companion server into that
environment, and then runs the demo. No cloud account, real identity provider, browser, production
credential, or Docker daemon is required.

## What it proves

The demo starts both local services on ephemeral loopback ports and then executes this sequence:

```text
real MCP client
  -> server/discover
  -> 401 + Protected Resource Metadata
  -> local OIDC discovery
  -> CIMD-first public client
  -> Authorization Code + PKCE
  -> RFC 9207 authorization-response issuer validation
  -> RFC 8707 resource-bound access token
  -> authenticated whoami
  -> health
  -> 403 insufficient_scope
  -> bounded reauthorization with prior + health scope
  -> successful health
  -> elevated whoami
  -> deliberately wrong-audience JWT
  -> 401 rejection
  -> legacy-looking Mcp-Session-Id probe
  -> successful request with no protocol session minted
```

The authorization-server counters are also checked. The expected evidence is:

- zero Dynamic Client Registrations because CIMD is preferred;
- two authorization requests;
- two token exchanges;
- initial scope `mcp:tools:call`;
- elevated scope `mcp:tools:call mcp:tools:health`;
- wrong resource audience rejected with HTTP `401`;
- negotiated MCP protocol exactly `2026-07-28`;
- no `Mcp-Session-Id` returned.

## Output

A successful human-readable run ends with:

```text
P1.7a REFERENCE DEMO PASSED
OAuth:    CIMD-first Authorization Code + PKCE
MCP:      2026-07-28, authenticated whoami + health
Step-up:  mcp:tools:call -> + mcp:tools:health
Audience: wrong-resource JWT rejected with HTTP 401
State:    no protocol-level session minted
```

The same run also emits a JSON evidence summary. With `--json`, only that summary is written to
stdout, which makes the demo reusable by later CI and portfolio automation.

## Containerized follow-up

P1.7b reuses this same evidence scenario in Docker Compose while preserving the loopback-only
HTTP development boundary. See [Docker Compose reference demo](COMPOSE_DEMO.md).

## Security boundary

This is deliberately a local reference demo, not an identity-provider simulator for production:

- the OIDC issuer binds only to `127.0.0.1`;
- keys and identities are synthetic and generated for the local process;
- access tokens are kept in memory and are never printed;
- no real browser is opened;
- no external credential is read;
- child-process output is retained only in an ephemeral temporary directory for failure
  diagnostics and removed at the end of the run;
- the real server still performs signature, issuer, audience, expiry, and scope validation.

For the broader positive and fail-closed matrix, use [Cross-repository E2E](E2E.md).
