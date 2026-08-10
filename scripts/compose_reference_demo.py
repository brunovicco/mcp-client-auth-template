"""Run the P1.7b scenario against services already started by Docker Compose."""

import argparse
import asyncio
import json
from collections.abc import Sequence

from scripts.reference_demo import DemoError, ReferenceTopology, run_reference_scenario

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


async def _run(server_url: str, issuer: str, *, quiet: bool) -> dict[str, object]:
    topology = ReferenceTopology(
        issuer=issuer.rstrip("/"),
        server_url=server_url.rstrip("/"),
    )
    return await run_reference_scenario(
        topology,
        quiet=quiet,
        execution="docker-compose-shared-loopback",
    )


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
