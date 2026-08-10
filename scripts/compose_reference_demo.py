"""Run the P1.7b scenario against services already started by Docker Compose."""

import argparse
import asyncio
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from a2a_otel_kit.entrypoints.observability import Observability
from scripts.reference_demo import DemoError, ReferenceTopology, run_reference_scenario

from mcp_client_auth_template.entrypoints.demo_client import build_observability_settings

_DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
_DEFAULT_ISSUER = "http://127.0.0.1:9000"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run P1.7b against the shared-loopback Docker Compose topology."
    )
    parser.add_argument("--server-url", default=_DEFAULT_SERVER_URL)
    parser.add_argument("--issuer", default=_DEFAULT_ISSUER)
    parser.add_argument("--json", action="store_true")
    return parser


def _walk(value: Any) -> list[Any]:
    items = [value]
    if isinstance(value, dict):
        for nested in value.values():
            items.extend(_walk(nested))
    elif isinstance(value, list):
        for nested in value:
            items.extend(_walk(nested))
    return items


def _receipt_has_trace(receipt: Path, trace_id: str) -> bool:
    if not receipt.exists():
        return False

    names: set[str] = set()
    text = receipt.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for item in _walk(payload):
            if not isinstance(item, dict):
                continue
            item_trace_id = item.get("traceId", item.get("trace_id"))
            name = item.get("name")
            if str(item_trace_id) == trace_id and isinstance(name, str):
                names.add(name)

    required = {"mcp.client.streamable_http", "mcp.server.streamable_http"}
    return required.issubset(names)


def _wait_for_collector_receipt(trace_id: str) -> None:
    raw_path = os.environ.get("P1_7C_RECEIPT_PATH")
    if raw_path is None:
        return

    receipt = Path(raw_path)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if _receipt_has_trace(receipt, trace_id):
            print(f"[P1.7c] positive client/server Collector receipt: {trace_id}")
            return
        time.sleep(0.25)

    raise DemoError(
        f"Collector did not positively receive MCP client/server spans for trace_id={trace_id}"
    )


async def _run(server_url: str, issuer: str, *, quiet: bool) -> dict[str, object]:
    topology = ReferenceTopology(
        issuer=issuer.rstrip("/"),
        server_url=server_url.rstrip("/"),
    )
    observability = Observability.configure(build_observability_settings())

    try:
        with observability.start_span(
            "demo.reference_flow",
            attributes={"operation": "reference_flow"},
            record_exception=False,
        ) as root_span:
            trace_id = f"{root_span.get_span_context().trace_id:032x}"
            summary = await run_reference_scenario(
                topology,
                quiet=quiet,
                execution="docker-compose-shared-loopback",
                observability=observability,
            )

        summary["telemetry"] = {
            "enabled": observability.settings.enabled,
            "otlp_endpoint": observability.settings.otlp_endpoint,
            "trace_id": trace_id,
        }

        if not observability.flush():
            raise DemoError("OpenTelemetry client force_flush timed out")

        _wait_for_collector_receipt(trace_id)
        return summary
    finally:
        observability.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    quiet = bool(args.json)
    try:
        summary = asyncio.run(_run(args.server_url, args.issuer, quiet=quiet))
    except (DemoError, OSError, RuntimeError) as exc:
        print(f"P1.7b compose reference demo FAILED: {exc}")
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print()
        print("============================================================")
        print("P1.7b COMPOSE REFERENCE DEMO PASSED")
        print("Topology: client + server + fake OIDC in isolated containers")
        print("Network:  shared namespace with real 127.0.0.1 loopback")
        print("Server:   immutable published v0.5.0 image by digest")
        print("OAuth:    CIMD-first Authorization Code + PKCE")
        print("Catalog:  protected tools hidden from anonymous tools/list")
        print("Step-up:  mcp:tools:call -> + mcp:tools:health")
        print("Security: wrong audience rejected; no MCP session minted")
        print("============================================================")
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
