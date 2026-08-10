# P1.7b — Docker Compose reference demo

P1.7b packages the validated P1.7a OAuth/MCP scenario into three containers while preserving the
existing network-security boundary.

```text
fake OIDC :9000  <──>  MCP Server :8000  <──>  demo client
     127.0.0.1              127.0.0.1             127.0.0.1
```

The OIDC and demo services join the Server container's network namespace. This is deliberate:
ordinary Compose DNS names resolve to private bridge addresses, while these repositories permit
insecure HTTP only for explicit loopback development. P1.7b therefore keeps real `127.0.0.1`
traffic instead of adding a Docker-specific SSRF/TLS bypass.

Run:

```bash
./scripts/run_compose_demo.sh
```

The server is consumed by immutable digest:

```text
ghcr.io/brunovicco/mcp-server-auth-template@sha256:4a220992b5df2382b2f821713b8b4c840469e4465395cbdeb1349dee0f8a1110
```

The demo reuses the exact P1.7a scenario and proves MCP `2026-07-28`, CIMD-first Authorization
Code + PKCE, protected anonymous catalog behavior, authenticated `whoami`, bounded scope step-up
through `health`, wrong-audience rejection, and no MCP session state.

The JSON summary marks the execution topology as `docker-compose-shared-loopback`.

All services use read-only root filesystems, ephemeral `/tmp`, dropped Linux capabilities,
`no-new-privileges`, and no host ports. No Docker socket, host source mount, production credential,
or persistent token volume is exposed.

This is a deterministic local demo topology, not a recommended production deployment model.
