# mcp-client-auth-template

[![quality](https://github.com/brunovicco/mcp-client-auth-template/actions/workflows/quality.yml/badge.svg)](https://github.com/brunovicco/mcp-client-auth-template/actions/workflows/quality.yml)
![python](https://img.shields.io/badge/python-3.13-blue.svg)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

*[Leia em português](README.pt-BR.md)*

A reusable template for a native/CLI MCP client that authenticates against an OAuth 2.1
authorization server - Microsoft Entra ID or any standards-compliant OIDC authorization server
(Auth0, Keycloak, WorkOS AuthKit, ...) - and then calls tools on an MCP resource server.
Targets the MCP **2026-07-28** specification. This is the client-side half of the pattern in
[`mcp-server-auth-template`](https://github.com/brunovicco/mcp-server-auth-template); the two
are meant to be run against each other, and each stands on its own as a starting point too.

The official `mcp` SDK's `OAuthClientProvider` already implements PRM discovery, AS metadata
discovery, PKCE, CIMD-first client registration with automatic Dynamic Client Registration
fallback, token refresh, and RFC 9207 issuer validation. Entra ID also can't be registered
dynamically (no DCR, no CIMD), so a real integration needs a pre-registered client either way.
This template supplies exactly the pieces the SDK asks an embedding app to provide - token
storage, opening a browser, and receiving the redirect - built once, correctly, so a new MCP
client doesn't have to re-derive an RFC 8252 loopback server or Entra's pre-registration
handling from scratch. See `docs/adr/0002-oauth21-native-client.md` for the full reasoning.

## Auth quick start

1. Copy `.env.example` to `.env` and fill in one of the two provider blocks (Entra ID or a
   generic OIDC authorization server), and point `MCP_CLIENT_SERVER_URL` at a running instance
   of the server template.
2. Run the demo:

   ```bash
   uv run python -m mcp_client_auth_template.entrypoints.demo_client
   ```
3. The first run opens your browser for the authorization code + PKCE flow, waits on a local
   loopback listener (`http://127.0.0.1:8765/callback` by default) for the redirect, exchanges
   the code for tokens, and calls the server's `whoami` and `health` tools.
4. With `MCP_CLIENT_TOKEN_STORAGE_PATH` set (the default,
   `~/.mcp-client-auth-template/tokens.json`), later runs reuse and silently refresh the stored
   token instead of prompting again. Unset it to fall back to in-memory-only storage.

Swap `MCP_CLIENT_AUTH_PROVIDER` between `entra` and `generic` to switch adapters - no other code
changes. See `src/mcp_client_auth_template/adapters/` for the two provider factories and
`tests/unit/test_*_client_auth.py` for how each is tested offline (a fake in-memory token
store, no network, no real IdP needed).

## Development

```bash
uv lock --check
uv sync --frozen --all-groups --extra observability
uv run pytest
uv run python scripts/quality_gate.py
```

List or select gate checks with `--list` and `--check NAME`. See `AGENTS.md` for build, lint,
format, typecheck, test, security, architecture, MCP, and completion requirements, and
`docs/DEVELOPMENT.md` for the container build and local setup.

Codex loads the checked-in `.codex/config.toml`, `.codex/hooks.json`, and `.agents/skills/` only
within the appropriate project/trust context. Review lifecycle hooks with `/hooks` before use.

## License

[MIT](LICENSE)
