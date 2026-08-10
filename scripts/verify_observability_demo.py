"""Verify positive OpenTelemetry delivery for the P1.7c demo."""

import argparse
import http.client
import json
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

_RECEIPT = Path("/receipts/traces.jsonl")
_CLIENT_SPAN = "mcp.client.streamable_http"
_SERVER_SPAN = "mcp.server.streamable_http"
_REFERENCE_ROOT_SPAN = "demo.reference_flow"
_CLIENT_SERVICE = "mcp-client-auth-template"
_SERVER_SERVICE = "mcp-server-auth-template"
_COLLECTOR_HEALTH = "http://127.0.0.1:13133/"
_TEMPO_READY = "http://127.0.0.1:3200/ready"
_GRAFANA_HEALTH = "http://127.0.0.1:3000/api/health"
_GRAFANA_DATASOURCE = "http://127.0.0.1:3000/api/datasources/name/Tempo"
_FORBIDDEN_VALUES = (
    "https://client.example.invalid/oauth/client-metadata.json",
    "https://wrong-resource.example.invalid/",
    "mcp:tools:call",
    "mcp:tools:health",
)


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _load_spans() -> list[dict[str, Any]]:
    if not _RECEIPT.exists():
        return []

    spans: list[dict[str, Any]] = []
    text = _RECEIPT.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for item in _walk(payload):
            if not isinstance(item, dict) or "name" not in item:
                continue
            if "traceId" in item or "trace_id" in item:
                spans.append(cast(dict[str, Any], item))
    return spans


def _trace_id(span: dict[str, Any]) -> str | None:
    value = span.get("traceId", span.get("trace_id"))
    return str(value) if value is not None else None


def _reference_trace_id(spans: list[dict[str, Any]]) -> str | None:
    root_trace_ids = {
        trace_id
        for span in spans
        if span.get("name") == _REFERENCE_ROOT_SPAN
        if (trace_id := _trace_id(span)) is not None
    }
    if len(root_trace_ids) != 1:
        return None

    trace_id = next(iter(root_trace_ids))
    names = {
        str(span["name"])
        for span in spans
        if _trace_id(span) == trace_id and span.get("name") is not None
    }
    required = {_REFERENCE_ROOT_SPAN, _CLIENT_SPAN, _SERVER_SPAN}
    return trace_id if required.issubset(names) else None


def _get(url: str, timeout: float = 2.0) -> tuple[int, bytes]:
    parsed = urlsplit(url)

    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise RuntimeError(f"unexpected observability endpoint: {url}")

    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError("userinfo is not permitted in observability endpoints")

    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    connection = http.client.HTTPConnection(
        parsed.hostname,
        port,
        timeout=timeout,
    )
    try:
        connection.request(
            "GET",
            path,
            headers={"Accept": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _wait_http(url: str, label: str, timeout_seconds: float = 30.0) -> bytes:
    deadline = time.monotonic() + timeout_seconds
    last_status = 0
    while time.monotonic() < deadline:
        try:
            status, body = _get(url)
        except OSError:
            status, body = 0, b""
        last_status = status
        if 200 <= status < 300:
            print(f"✓ {label} ready")
            return body
        time.sleep(0.25)
    raise RuntimeError(f"{label} not ready; last HTTP status={last_status}")


def wait_stack() -> None:
    _wait_http(_COLLECTOR_HEALTH, "OpenTelemetry Collector")
    _wait_http(_TEMPO_READY, "Tempo")
    _wait_http(_GRAFANA_HEALTH, "Grafana")
    print("Observability stack: READY")


def _wait_reference_trace(
    timeout_seconds: float = 30.0,
) -> tuple[str, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        spans = _load_spans()
        trace_id = _reference_trace_id(spans)
        if trace_id is not None:
            return trace_id, spans
        time.sleep(0.25)

    spans = _load_spans()
    names = sorted({str(span.get("name")) for span in spans if span.get("name") is not None})
    root_ids = sorted(
        {
            trace_id
            for span in spans
            if span.get("name") == _REFERENCE_ROOT_SPAN
            if (trace_id := _trace_id(span)) is not None
        }
    )
    raise RuntimeError(
        "timed out waiting for the P1.7c root trace with MCP client/server spans; "
        f"root_trace_ids={root_ids}; observed span names={names}"
    )


def _verify_service_resources(raw: str) -> None:
    missing = [service for service in (_CLIENT_SERVICE, _SERVER_SERVICE) if service not in raw]
    if missing:
        raise RuntimeError(f"Collector receipt missing service resources: {missing}")
    print(f"✓ service resources found: {_CLIENT_SERVICE}, {_SERVER_SERVICE}")


def _verify_privacy(raw: str) -> None:
    leaked = [value for value in _FORBIDDEN_VALUES if value in raw]
    if leaked:
        raise RuntimeError(f"OAuth/MCP fixture values leaked into traces: {leaked}")
    lower = raw.lower()
    if "bearer eyj" in lower or '"authorization"' in lower:
        raise RuntimeError("credential-shaped authorization content leaked into traces")
    print("✓ OAuth tokens/scopes/resource identifiers absent from trace telemetry")


def _wait_tempo_trace(trace_id: str, timeout_seconds: float = 30.0) -> None:
    url = f"http://127.0.0.1:3200/api/traces/{trace_id}"
    deadline = time.monotonic() + timeout_seconds
    last_status = 0
    while time.monotonic() < deadline:
        try:
            status, _body = _get(url)
        except OSError:
            status = 0
        last_status = status
        if status == 200:
            print(f"✓ Tempo returned distributed trace: {trace_id}")
            return
        time.sleep(0.5)
    raise RuntimeError(f"Tempo did not return trace {trace_id}; last HTTP status={last_status}")


def _verify_grafana_datasource() -> None:
    status, body = _get(_GRAFANA_DATASOURCE)
    if status != 200:
        raise RuntimeError(f"Grafana Tempo datasource returned HTTP {status}")

    payload = json.loads(body)
    expected = {
        "name": "Tempo",
        "type": "tempo",
        "url": "http://127.0.0.1:3200",
    }
    if not isinstance(payload, dict):
        raise RuntimeError("Grafana datasource response is not a JSON object")
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(f"Grafana Tempo datasource mismatch: {payload}")
    print("✓ Grafana Tempo datasource provisioned")


def verify_traces() -> None:
    trace_id, spans = _wait_reference_trace()
    raw = _RECEIPT.read_text(encoding="utf-8", errors="replace")

    current_names = {
        str(span["name"])
        for span in spans
        if _trace_id(span) == trace_id and span.get("name") is not None
    }
    required = {_CLIENT_SPAN, _SERVER_SPAN}
    if not required.issubset(current_names):
        raise RuntimeError("shared trace is missing required MCP client/server spans")

    print(f"✓ P1.7c root + MCP client/server share trace_id: {trace_id}")
    _verify_service_resources(raw)
    _verify_privacy(raw)
    _wait_tempo_trace(trace_id)
    _verify_grafana_datasource()

    print()
    print("============================================================")
    print("P1.7c OBSERVABILITY DEMO PASSED")
    print(f"Trace:     {trace_id}")
    print("Collector: positive OTLP receipt")
    print("Context:   MCP client/server share one trace_id")
    print("Tempo:     trace query succeeded")
    print("Grafana:   Tempo datasource provisioned")
    print("Privacy:   OAuth/MCP sensitive values absent")
    print("============================================================")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--wait-stack", action="store_true")
    mode.add_argument("--verify-traces", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.wait_stack:
            wait_stack()
        else:
            verify_traces()
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"P1.7c observability verification FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
