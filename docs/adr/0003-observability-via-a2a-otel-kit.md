# ADR-0003: Observability via `a2a-otel-kit`, not the harness's generic scaffolding

- Status: Superseded by ADR-0019
- Date: 2026-08-07

## Context

> Historical note: this ADR records the original `a2a-otel-kit` 0.4 integration. Its HTTPX
> compatibility workaround and dependency constraints were superseded by ADR-0019 after version
> 0.6 added native MCP SDK 2.x/HTTPX2 support.

`codex-python-engineering-harness` scaffolds a generic, MCP-unaware observability stack:
`adapters/observability.py` (a hand-rolled OpenTelemetry `TelemetryLifecycle`/`SafeTracer`/
`SafeSpan`), `adapters/tracing.py` (a Langfuse LLM-call observer), and
`entrypoints/logging.py` (a structlog bootstrap). None of the three was ever called from
`entrypoints/demo_client.py` except `configure_logging()` — the OTel lifecycle and the Langfuse
observer were dead code, kept alive only by their own test files and the `observability`/`tracing`
`uv sync` extras.

`a2a-otel-kit` (PyPI, MIT, `github.com/brunovicco/a2a-otel-kit`) is a small, already-published
library extracted from `multi-agent-credit-desk` that provides exactly this project's shape of
observability need: `Observability.configure()`/`ObservabilitySettings` (a single facade over an
OpenTelemetry tracer and a structlog bootstrap, silent unless explicitly enabled), an
allowlist-based attribute sanitizer, W3C trace-context propagation, and — behind its optional `mcp`
extra — two ready-made Streamable HTTP adapters: `TracingASGIMiddleware` for a server exposing an
ASGI app, and `TracingAsyncTransport` for a client passing a custom transport into
`streamable_http_client(http_client=...)`.

This repository is the **client** half of the `mcp-client-auth-template` /
`mcp-server-auth-template` pair — there is no `entrypoints/mcp_server.py` and no ASGI app here — so
only `TracingAsyncTransport` applies; `TracingASGIMiddleware` is out of scope for this repo.

## Decision

- Delete `adapters/observability.py`, `adapters/tracing.py`, and `entrypoints/logging.py`
  outright, not merge them into thinner wrappers. All three were unused; `a2a-otel-kit`'s
  `Observability`/`ObservabilitySettings` covers the tracer lifecycle and the structured-logging
  bootstrap, and nothing in this client makes an LLM call for the Langfuse observer to have ever
  had anything to trace.
- Wire `Observability.configure(...)` into `entrypoints/demo_client.py::run_demo()` for real:
  `build_observability_settings()` builds `ObservabilitySettings` with this demo's fixed
  service identity; everything else (`enabled`, `otlp_endpoint`, `otlp_timeout_seconds`,
  `log_level`, `log_format`) is read from `A2A_OTEL_*`-prefixed environment variables by
  `ObservabilitySettings` itself. `enabled` defaults to `False`, preserving the "network-silent
  unless explicitly configured" contract the deleted `adapters/observability.py` used to document.
- Wrap the mcp SDK's HTTP transport with `TracingAsyncTransport.wrap(...)` so every MCP
  Streamable HTTP request carries a W3C `traceparent`/`tracestate` header and produces one CLIENT
  span (`mcp.client.streamable_http`), without ever reading a request or response body. Call
  `observability.shutdown()` in a `finally` around the demo's request lifecycle so pending spans
  flush even on error; it is a safe no-op when observability is disabled.
- `a2a-otel-kit` and `httpx` (see below) are **core** dependencies of this project, not an optional
  extra. The `observability`/`tracing` `uv sync` extras are removed entirely. This is a deliberate
  reversal of the harness's own convention: those extras existed only to keep unused packages out
  of the default install; now that `a2a-otel-kit` sits in `demo_client.py`'s actual request path,
  making it optional would mean the demo cannot run at all without remembering to install an extra
  first, not merely that tracing would be disabled. Network silence is guaranteed by
  `ObservabilitySettings.enabled=False`, not by the package being absent.

### Two verified compatibility findings

1. **`a2a-otel-kit[mcp]`'s own extra cannot be installed here.** Its `mcp` extra declares
   `Requires-Dist: mcp<2.0,>=1.28`, which is mutually exclusive with this project's `mcp>=2.0,<3`
   pin (ADR-0002). `uv add "a2a-otel-kit[mcp]"` fails to resolve. Reading
   `a2a_otel_kit/adapters/mcp.py`'s actual source shows its only runtime use of that extra is
   `importlib.metadata.version("mcp")` — a presence check, not a version check — followed by
   `import httpx`. Neither requires the pinned v1 SDK. Resolution: install `a2a-otel-kit` without
   the `mcp` extra and add plain `httpx` (distinct from the mcp SDK's vendored `httpx2` fork) as
   its own direct dependency.
2. **`TracingAsyncTransport` and the mcp SDK's `httpx2` transport are runtime-compatible without
   any shim**, verified by reading both libraries' source, not by assumption:
   `TracingAsyncTransport` subclasses `httpx.AsyncBaseTransport` and type-hints
   `httpx.Request`/`httpx.Response`, but its method bodies only touch duck-typed attributes
   (`request.headers`, `response.status_code`) and delegate to `self._inner.handle_async_request`.
   `httpx2.AsyncClient._init_transport()` performs no `isinstance` check on the `transport` it is
   given. Wrapping an `httpx2.AsyncHTTPTransport` and handing the result to
   `httpx2.AsyncClient(transport=...)` works at runtime unmodified — confirmed with an offline
   smoke test during implementation.
   - This does **not** extend to `mypy --strict`: `httpx.AsyncBaseTransport` and
     `httpx2.AsyncBaseTransport` are nominally distinct classes from separate packages, so mypy
     flags both boundary calls. `httpx2` exposes `alias_httpx()`, a process-wide `sys.meta_path`
     hook that redirects `import httpx` to `httpx2` — but it only affects runtime import
     resolution; mypy resolves imports statically and never executes it, so it would not resolve
     the type error and would only add a fragile, import-order-dependent global side effect for no
     benefit. **Rejected.** Instead, `demo_client.py` uses two `cast(...)` calls at the exact
     boundary points, matching the `cast(str, settings.entra_client_id)` pattern the file already
     uses for SDK type-narrowing, with a comment recording why the cast is sound.

## Consequences

- `docs/LLM_OBSERVABILITY.md` is renamed to `docs/OBSERVABILITY.md` and rewritten: the Langfuse
  section and its content-capture approval checklist are removed entirely (no LLM call exists in
  this client for either to apply to); the OTel section is rewritten around `ObservabilitySettings`
  and `a2a_otel_kit.domain.attributes.sanitize_attributes`'s allowlist.
- `pyproject.toml` drops `[project.optional-dependencies]` entirely and drops the direct
  `opentelemetry-*` pins (now transitive through `a2a-otel-kit`, which fixes its own compatible
  versions) and the `langfuse` mypy override.
- Every `uv sync --frozen --all-groups --extra observability` invocation across
  `README.md`/`README.pt-BR.md`/`CONTRIBUTING.md`/`AGENTS.md`/`CLAUDE.md`/`docs/DEVELOPMENT.md`/
  `.github/workflows/quality.yml` drops `--extra observability`, since the extra no longer exists.
- Pinning `a2a-otel-kit>=0.4.2,<0.5` is deliberately narrow: this integration relies on
  `adapters/mcp.py`'s exact import-time behavior (a metadata presence check, not a version
  constraint) rather than on its declared `mcp` extra, so a future minor release changing that
  behavior should be caught by `tests/unit/test_demo_client_observability.py`'s wiring test before
  it reaches a consumer of this template.
- Adding a server-side ASGI app to this template later (there is none today) would need
  `TracingASGIMiddleware`, not `TracingAsyncTransport` — that adapter belongs in the sibling
  `mcp-server-auth-template` repo, not here.
