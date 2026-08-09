# Observability policy

Observability in this project is provided entirely by [`a2a-otel-kit`](https://github.com/brunovicco/a2a-otel-kit),
a small MIT-licensed library that bundles a vendor-neutral OpenTelemetry tracer, a structlog
bootstrap, and a Streamable HTTP transport adapter for the official `mcp` Python SDK. See
`docs/adr/0003-observability-via-a2a-otel-kit.md` for why it replaced this project's own
hand-rolled scaffolding and `docs/adr/0019-native-mcp-v2-observability.md` for the native MCP SDK
2.x boundary adopted with version 0.6.

## What is wired up

`entrypoints/demo_client.py::run_demo()` calls `Observability.configure(...)` once at startup,
which configures structured logging (structlog, JSON to stdout by default) and, when enabled, an
OpenTelemetry tracer exporting via OTLP/HTTP. It then wraps the mcp SDK's HTTP transport with
`a2a_otel_kit.adapters.mcp.TracingAsyncTransport`, so every MCP Streamable HTTP request carries a
W3C `traceparent`/`tracestate` header and produces one CLIENT span
(`mcp.client.streamable_http`) — without ever reading a request or response body.
`observability.shutdown()` runs in a `finally` block so pending spans flush even when a run fails.
The adapter and the MCP SDK now share the same `httpx2.AsyncBaseTransport` contract directly; no
plain-HTTPX dependency, nominal cross-package casts, or import aliasing is involved.

## Default behavior

Observability is disabled by default (`ObservabilitySettings.enabled=False`): no tracer provider,
no exporter, and no network connection are constructed unless explicitly turned on. Structured
logging to stdout is always active; it never depends on `enabled`.

## Configuration reference

All fields are read from `A2A_OTEL_`-prefixed environment variables by
`a2a_otel_kit.application.settings.ObservabilitySettings`; `service_name`, `service_version`, and
`environment` are fixed by `build_observability_settings()` in `demo_client.py` for this
single-entrypoint demo and are not configurable via environment.

| Variable | Required | Purpose |
|---|---|---|
| `A2A_OTEL_ENABLED` | no (default `false`) | `true` turns on the OTel tracer and OTLP export |
| `A2A_OTEL_OTLP_ENDPOINT` | required when `A2A_OTEL_ENABLED=true` | Complete OTLP HTTP traces endpoint, for example `http://localhost:4318/v1/traces` |
| `A2A_OTEL_OTLP_TIMEOUT_SECONDS` | no (default `10.0`) | Export request timeout |
| `A2A_OTEL_LOG_LEVEL` | no (default `INFO`) | Standard-library log level name |
| `A2A_OTEL_LOG_FORMAT` | no (default `json`) | `json` or `console` |

`ObservabilitySettings` rejects `A2A_OTEL_ENABLED=true` without an `A2A_OTEL_OTLP_ENDPOINT` at
construction time, so a misconfigured "half-enabled" state cannot exist.

## Privacy and the attribute allowlist

Span and structured-log attributes pass through
`a2a_otel_kit.domain.attributes.sanitize_attributes` before they leave the process: only a fixed,
non-content allowlist survives (`service`, `environment`, `version`, `component`, `operation`,
`outcome`, `correlation_id`, `request_id`, `retry_count`, `duration_ms`, `http.method`,
`http.status_code`, `error.type`), values are bounded scalars, and any key matching a
credential-shaped pattern (`password`, `secret`, `token`, `authoriz*`, `cookie`, `api[_-]?key`,
`credential`, `private[_-]?key`, `ssn`, `social[_-]?security`, `access[_-]?key`) is rejected even
if a caller tries to allowlist it explicitly. `TracingAsyncTransport` never reads a request or
response body, so no authorization header, token, or MCP tool payload can reach a span in the
first place.

Never widen this allowlist to make something easier to log — see the security and observability
contract in `AGENTS.md` for the project-wide rule this reinforces.

## Uninstrumented by default

Leaving `A2A_OTEL_ENABLED` unset (or `false`) keeps every run fully untraced except for structured
logs to stdout: `Observability.configure(...)` still runs, but constructs no exporter and attempts
no network connection. This matches the harness's MCP governance model: nothing external is
connected until a project deliberately opts in.
