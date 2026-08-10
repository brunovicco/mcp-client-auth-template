"""One-command, headless reference demo for the paired MCP auth templates."""

import argparse
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.demo_client import build_mcp_client, build_oauth_provider
from mcp_client_auth_template.entrypoints.settings import Settings

_CLIENT_ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_SCOPE = "mcp:tools:call"
_HEALTH_SCOPE = "mcp:tools:health"
_PROTOCOL_VERSION = "2026-07-28"
_CIMD_CLIENT_ID = "https://client.example.invalid/oauth/client-metadata.json"
_WRONG_AUDIENCE = "https://wrong-resource.example.invalid/"
_HEADER_SESSION_PROBE = "legacy-session-id-must-not-create-state"


class DemoError(RuntimeError):
    """Raised when a reference-demo invariant is not satisfied."""


@dataclass
class _RunningProcess:
    process: subprocess.Popen[str]
    log_path: Path
    log_file: TextIO

    def stop(self) -> None:
        """Terminate a child process and close its log file."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_file.close()

    def diagnostics(self) -> str:
        """Return bounded local child-process diagnostics."""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-8_000:]


@dataclass(frozen=True)
class ReferenceTopology:
    issuer: str
    server_url: str


@dataclass
class _AuthorizationRedirectHarness:
    result: AuthorizationCodeResult | None = None

    async def redirect(self, url: str) -> None:
        """Follow the local authorization request only as far as the callback redirect."""
        async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
            response = await client.request("GET", url)
            await response.aread()
        if response.status_code not in {302, 303, 307, 308}:
            raise DemoError(f"authorization endpoint returned HTTP {response.status_code}")
        values = parse_qs(urlsplit(response.headers["location"]).query)
        try:
            self.result = AuthorizationCodeResult(
                code=values["code"][0],
                state=values["state"][0],
                iss=values.get("iss", [None])[0],
            )
        except (KeyError, IndexError) as exc:
            raise DemoError("authorization redirect did not contain code/state") from exc

    async def callback(self) -> AuthorizationCodeResult:
        """Return the callback captured by :meth:`redirect`."""
        if self.result is None:
            raise DemoError("authorization callback was requested before redirect capture")
        return self.result


def _free_port() -> int:
    """Return an ephemeral local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _resolve_server_root(explicit: Path | None, *, client_root: Path = _CLIENT_ROOT) -> Path:
    """Resolve and validate the companion server checkout."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    else:
        configured = os.environ.get("MCP_DEMO_SERVER_ROOT")
        if configured:
            candidates.append(Path(configured))
        candidates.append(client_root.parent / "mcp-server-auth-template")

    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "pyproject.toml").is_file() and (
            resolved / "src/mcp_server_auth_template"
        ).is_dir():
            return resolved

    rendered = ", ".join(str(candidate) for candidate in candidates)
    raise DemoError(
        "companion server checkout not found; clone mcp-server-auth-template beside the client "
        f"or set MCP_DEMO_SERVER_ROOT (checked: {rendered})"
    )


def _start_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> _RunningProcess:
    """Start one trusted local child process with bounded file-backed diagnostics."""
    log_file = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(  # noqa: S603
            list(command),
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        log_file.close()
        raise
    return _RunningProcess(process=process, log_path=log_path, log_file=log_file)


def _wait_for_port(
    port: int,
    process: _RunningProcess,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    """Wait for a local listener or fail with child-process diagnostics."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise DemoError(
                "child process exited during startup:\n"
                f"{process.diagnostics() or '<no child output>'}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise DemoError(
        f"timed out waiting for localhost:{port}\n{process.diagnostics() or '<no child output>'}"
    )


@asynccontextmanager
async def _topology(server_root: Path, *, work_dir: Path) -> AsyncIterator[ReferenceTopology]:
    """Start the fake OIDC AS and the actual companion MCP server."""
    as_port = _free_port()
    server_port = _free_port()
    issuer = f"http://127.0.0.1:{as_port}"
    server_url = f"http://127.0.0.1:{server_port}"

    fake_env = os.environ.copy()
    fake_env["FAKE_OIDC_ISSUER"] = issuer
    fake_as = _start_process(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "scripts.e2e_fake_oidc_as:app",
            "--app-dir",
            str(_CLIENT_ROOT),
            "--host",
            "127.0.0.1",
            "--port",
            str(as_port),
            "--log-level",
            "warning",
        ],
        cwd=_CLIENT_ROOT,
        env=fake_env,
        log_path=work_dir / "fake-oidc.log",
    )

    try:
        _wait_for_port(as_port, fake_as)

        server_env = os.environ.copy()
        existing_pythonpath = server_env.get("PYTHONPATH")
        server_src = str(server_root / "src")
        server_env["PYTHONPATH"] = (
            f"{server_src}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else server_src
        )
        server_env.update(
            {
                "MCP_SERVER_RESOURCE_SERVER_URL": f"{server_url}/",
                "MCP_SERVER_REQUIRED_SCOPES": json.dumps([_REQUIRED_SCOPE]),
                "MCP_SERVER_AUTH_PROVIDER": "generic",
                "MCP_SERVER_GENERIC_ISSUER_URL": issuer,
                "MCP_SERVER_GENERIC_AUDIENCE": f"{server_url}/",
                "MCP_SERVER_OIDC_ALLOW_INSECURE_LOOPBACK": "true",
                "LOG_LEVEL": "WARNING",
            }
        )
        server = _start_process(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mcp_server_auth_template.entrypoints.mcp_server:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(server_port),
                "--log-level",
                "warning",
            ],
            cwd=server_root,
            env=server_env,
            log_path=work_dir / "mcp-server.log",
        )

        try:
            _wait_for_port(server_port, server)
            yield ReferenceTopology(issuer=issuer, server_url=server_url)
        finally:
            server.stop()
    finally:
        fake_as.stop()


async def _json_request(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Call a local demo-control endpoint and decode a JSON object."""
    headers: dict[str, str] = {}
    content: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(payload).encode("utf-8")

    async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
        response = await client.request(method, url, headers=headers, content=content)
        body = await response.aread()

    if response.status_code != 200:
        raise DemoError(
            f"local control endpoint returned HTTP {response.status_code}: "
            f"{body.decode('utf-8', errors='replace')[:500]}"
        )
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise DemoError("local control endpoint did not return a JSON object")
    return cast(dict[str, object], decoded)


async def _mint_wrong_audience_token(topology: ReferenceTopology) -> str:
    """Mint a synthetic token bound to a different resource."""
    response = await _json_request(
        "POST",
        f"{topology.issuer}/__test__/mint",
        {
            "audience": _WRONG_AUDIENCE,
            "issuer": topology.issuer,
            "scope": f"{_REQUIRED_SCOPE} {_HEALTH_SCOPE}",
            "expires_in": 300,
        },
    )
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise DemoError("fake authorization server did not mint a wrong-audience token")
    return token


async def _protected_request_status(topology: ReferenceTopology, token: str) -> int:
    """Exercise the server bearer boundary without exposing token contents."""
    async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
        response = await client.request(
            "POST",
            f"{topology.server_url}/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": _PROTOCOL_VERSION,
            },
            content=b"{}",
        )
        await response.aread()
    return response.status_code


def _modern_tool_request() -> dict[str, object]:
    """Build one MCP 2026-07-28 self-describing tool call."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "whoami",
            "arguments": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": _PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "p1.7a-reference-demo",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


async def _stateless_probe(topology: ReferenceTopology, token: str) -> bool:
    """Prove that a legacy-looking session header does not create protocol session state."""
    async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
        response = await client.request(
            "POST",
            f"{topology.server_url}/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": _PROTOCOL_VERSION,
                "Mcp-Method": "tools/call",
                "Mcp-Name": "whoami",
                "Mcp-Session-Id": _HEADER_SESSION_PROBE,
            },
            json=_modern_tool_request(),
        )
        await response.aread()
    return response.status_code == 200 and "mcp-session-id" not in response.headers


def _require_dict(value: object, label: str) -> dict[str, object]:
    """Narrow one structured-content value or fail with a stable demo error."""
    if not isinstance(value, dict):
        raise DemoError(f"{label} did not return structured content")
    return cast(dict[str, object], value)


class _FilteredCatalogWarning(logging.Filter):
    """Hide only the SDK warning caused by this demo's authorization-filtered tool catalog."""

    _SUFFIX = "not listed by server, cannot validate any structured content"

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (message.startswith("Tool ") and message.endswith(self._SUFFIX))


class _SdkCatalogWarningFilter:
    """Install and remove the narrow SDK warning filter around authenticated tool calls."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("client")
        self._filter = _FilteredCatalogWarning()

    def __enter__(self) -> None:
        self._logger.addFilter(self._filter)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._logger.removeFilter(self._filter)


def _step(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(f"[P1.7a] {message}", flush=True)


async def run_reference_scenario(
    topology: ReferenceTopology,
    *,
    quiet: bool,
    execution: str,
) -> dict[str, object]:
    """Run the reusable P1.7 OAuth/MCP evidence scenario against ready local endpoints."""
    _step("enabling CIMD-first interactive OAuth profile", quiet=quiet)
    await _json_request(
        "POST",
        f"{topology.issuer}/__test__/configure",
        {"client_id_metadata_document_supported": True},
    )

    settings = Settings(
        auth_provider="generic",
        server_url=topology.server_url,
        scope=_REQUIRED_SCOPE,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
        generic_client_metadata_url=_CIMD_CLIENT_ID,
    )
    storage = InMemoryTokenStorage()
    redirect_harness = _AuthorizationRedirectHarness()
    oauth_provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=redirect_harness.redirect,
        callback_handler=redirect_harness.callback,
    )

    _step("authenticating headlessly and calling whoami", quiet=quiet)
    async with (
        httpx2.AsyncClient(
            auth=oauth_provider,
            follow_redirects=True,
            timeout=30.0,
        ) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        if client.protocol_version != _PROTOCOL_VERSION:
            raise DemoError(
                f"negotiated protocol {client.protocol_version!r}, expected {_PROTOCOL_VERSION!r}"
            )

        _step("proving protected tools are hidden from anonymous catalog discovery", quiet=quiet)
        anonymous_listing = await client.list_tools(cache_mode="bypass")
        if anonymous_listing.tools:
            names = ", ".join(sorted(tool.name for tool in anonymous_listing.tools))
            raise DemoError(f"anonymous tools/list unexpectedly exposed protected tools: {names}")

        # This server intentionally returns an authorization-filtered catalog. The SDK v2
        # re-runs tools/list after successful calls to discover output schemas and emits a
        # warning when the called tool is hidden from that catalog. The demo validates the
        # returned structured content directly below, so suppress only that exact SDK
        # warning while preserving every other client warning/error.
        with _SdkCatalogWarningFilter():
            initial_identity_result = await client.call_tool("whoami")
            initial_identity = _require_dict(
                initial_identity_result.structured_content,
                "initial whoami",
            )
            if initial_identity.get("authenticated") is not True:
                raise DemoError("initial whoami did not report an authenticated principal")
            if initial_identity.get("client_id") != _CIMD_CLIENT_ID:
                raise DemoError("CIMD client ID was not preserved into the authenticated principal")
            if initial_identity.get("scopes") != [_REQUIRED_SCOPE]:
                raise DemoError("initial token did not contain exactly the basic MCP scope")

            _step("calling health to trigger bounded 403 scope step-up", quiet=quiet)
            health_result = await client.call_tool("health")
            health = _require_dict(health_result.structured_content, "health")
            if health != {"status": "ok"}:
                raise DemoError(f"health returned unexpected content: {health}")

            elevated_identity_result = await client.call_tool("whoami")
            elevated_identity = _require_dict(
                elevated_identity_result.structured_content,
                "elevated whoami",
            )
            expected_scopes = [_REQUIRED_SCOPE, _HEALTH_SCOPE]
            if elevated_identity.get("scopes") != expected_scopes:
                raise DemoError(
                    "step-up did not preserve the original scope and add the health scope"
                )

    tokens = await storage.get_tokens()
    if tokens is None or not tokens.access_token:
        raise DemoError("no elevated access token remained in in-memory storage")
    expected_scope_union = f"{_REQUIRED_SCOPE} {_HEALTH_SCOPE}"
    if tokens.scope != expected_scope_union:
        raise DemoError(
            f"stored elevated scope is {tokens.scope!r}, expected {expected_scope_union!r}"
        )

    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    expected_state = {
        "registrations": 0,
        "authorizations": 2,
        "token_exchanges": 2,
        "authorization_scopes": [_REQUIRED_SCOPE, expected_scope_union],
        "client_credentials_exchanges": 0,
        "client_credentials_scopes": [],
    }
    if state != expected_state:
        raise DemoError(f"authorization-server evidence drifted: {state}")

    _step("proving exact audience binding with a wrong-resource JWT", quiet=quiet)
    wrong_audience_token = await _mint_wrong_audience_token(topology)
    wrong_audience_status = await _protected_request_status(
        topology,
        wrong_audience_token,
    )
    if wrong_audience_status != 401:
        raise DemoError(f"wrong-audience token returned HTTP {wrong_audience_status}, expected 401")

    _step("proving stateless MCP 2026 transport semantics", quiet=quiet)
    stateless = await _stateless_probe(topology, tokens.access_token)
    if not stateless:
        raise DemoError("legacy-looking MCP session header created or exposed session state")

    return {
        "status": "passed",
        "protocol_version": _PROTOCOL_VERSION,
        "topology": {
            "authorization_server": "synthetic-local-oidc",
            "resource_server": "real-companion-server",
            "external_credentials": False,
            "execution": execution,
        },
        "interactive_oauth": {
            "registration_mode": "cimd-first",
            "dynamic_registrations": 0,
            "authorizations": 2,
            "token_exchanges": 2,
            "initial_scopes": [_REQUIRED_SCOPE],
            "elevated_scopes": [_REQUIRED_SCOPE, _HEALTH_SCOPE],
        },
        "mcp_calls": {
            "anonymous_catalog_tools": [],
            "protected_catalog_hidden": True,
            "whoami_authenticated": True,
            "health": "ok",
            "step_up_completed": True,
            "structured_results_validated_by_demo": True,
        },
        "security_evidence": {
            "wrong_audience_status": wrong_audience_status,
            "wrong_audience_rejected": True,
            "stateless_transport": True,
            "response_minted_session_id": False,
        },
    }


async def _run(server_root: Path, *, quiet: bool) -> dict[str, object]:
    """Run P1.7a with process-managed local services and return its evidence summary."""
    with tempfile.TemporaryDirectory(prefix="mcp-p1.7a-") as temporary:
        work_dir = Path(temporary)
        _step("starting fake OIDC authorization server + real MCP server", quiet=quiet)

        async with _topology(server_root, work_dir=work_dir) as topology:
            return await run_reference_scenario(
                topology,
                quiet=quiet,
                execution="local-processes",
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the P1.7a headless OAuth/MCP reference demo against the companion server checkout."
        )
    )
    parser.add_argument(
        "--server-root",
        type=Path,
        default=None,
        help=(
            "Path to mcp-server-auth-template. Defaults to MCP_DEMO_SERVER_ROOT or a sibling "
            "../mcp-server-auth-template checkout."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only the machine-readable evidence summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    args = _parser().parse_args(argv)
    quiet = bool(args.json)

    try:
        server_root = _resolve_server_root(args.server_root)
        summary = asyncio.run(_run(server_root, quiet=quiet))
    except (DemoError, OSError, RuntimeError) as exc:
        print(f"P1.7a reference demo FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print()
        print("============================================================")
        print("P1.7a REFERENCE DEMO PASSED")
        print("OAuth:    CIMD-first Authorization Code + PKCE")
        print("MCP:      2026-07-28, authenticated whoami + health")
        print("Catalog:  protected tools hidden from anonymous tools/list")
        print("Step-up:  mcp:tools:call -> + mcp:tools:health")
        print("Audience: wrong-resource JWT rejected with HTTP 401")
        print("State:    no protocol-level session minted")
        print("============================================================")
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
