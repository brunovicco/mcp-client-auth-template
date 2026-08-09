"""Cross-repository OAuth/MCP E2E tests against the companion server template."""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull
from pydantic import AnyUrl

from mcp_client_auth_template.adapters.client_credentials_auth import (
    OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID,
)
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.demo_client import build_mcp_client, build_oauth_provider
from mcp_client_auth_template.entrypoints.settings import Settings

pytestmark = pytest.mark.e2e

_SERVER_ROOT_VALUE = os.environ.get("MCP_E2E_SERVER_ROOT")
if _SERVER_ROOT_VALUE is None:
    pytest.skip(
        "cross-repository E2E is opt-in; set MCP_E2E_SERVER_ROOT to the companion server checkout",
        allow_module_level=True,
    )
_SERVER_ROOT = Path(_SERVER_ROOT_VALUE).resolve()
if not (_SERVER_ROOT / "src/mcp_server_auth_template").is_dir():
    pytest.skip(f"invalid MCP_E2E_SERVER_ROOT: {_SERVER_ROOT}", allow_module_level=True)

_CLIENT_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_SCOPE = "mcp:tools:call"
_HEALTH_SCOPE = "mcp:tools:health"
_CALLBACK_URI = "http://127.0.0.1:8765/callback"
_PROTOCOL_VERSION = "2026-07-28"
_CIMD_CLIENT_ID = "https://client.example.invalid/oauth/client-metadata.json"
_MACHINE_CLIENT_ID = "mcp-e2e-machine"
_MACHINE_CLIENT_CREDENTIAL = "e2e-test-credential"
_HEADER_MISMATCH = -32020
_UNSUPPORTED_PROTOCOL_VERSION = -32022


@dataclass
class _RunningProcess:
    process: subprocess.Popen[str]

    def stop(self) -> None:
        """Terminate the child process and close its captured output stream."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()

    def output_if_exited(self) -> str:
        """Return captured output only when startup failed and the process exited."""
        if self.process.poll() is None or self.process.stdout is None:
            return ""
        return cast(str, self.process.stdout.read())


@dataclass(frozen=True)
class _Topology:
    issuer: str
    server_url: str
    fake_as: _RunningProcess
    server: _RunningProcess


@dataclass
class _AuthorizationRedirectHarness:
    result: AuthorizationCodeResult | None = None

    async def redirect(self, url: str) -> None:
        """Emulate the browser only up to the loopback redirect, then capture its parameters."""
        async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
            response = await client.request("GET", url)
            await response.aread()
        assert response.status_code in {302, 303, 307, 308}
        location = response.headers["location"]
        values = parse_qs(urlsplit(location).query)
        self.result = AuthorizationCodeResult(
            code=values["code"][0],
            state=values["state"][0],
            iss=values.get("iss", [None])[0],
        )

    async def callback(self) -> AuthorizationCodeResult:
        """Return the callback captured by :meth:`redirect`."""
        assert self.result is not None
        return self.result


def _free_port() -> int:
    """Reserve an ephemeral local port long enough to discover its number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_process(command: list[str], *, cwd: Path, env: dict[str, str]) -> _RunningProcess:
    """Start a trusted local Python child process with output captured for startup diagnostics."""
    # The command is assembled only from trusted local paths/constants.
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return _RunningProcess(process)


def _wait_for_port(port: int, process: _RunningProcess, *, timeout_seconds: float = 10.0) -> None:
    """Wait for a localhost listener or fail with child-process diagnostics."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            pytest.fail(f"child process exited during startup:\n{process.output_if_exited()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    pytest.fail(f"timed out waiting for localhost:{port}")


@pytest.fixture
async def topology() -> AsyncIterator[_Topology]:
    """Start the fake OIDC AS and real companion MCP server on ephemeral localhost ports."""
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
    )
    try:
        _wait_for_port(as_port, fake_as)

        server_env = os.environ.copy()
        existing_pythonpath = server_env.get("PYTHONPATH")
        server_src = str(_SERVER_ROOT / "src")
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
            cwd=_SERVER_ROOT,
            env=server_env,
        )
        try:
            _wait_for_port(server_port, server)
            yield _Topology(issuer=issuer, server_url=server_url, fake_as=fake_as, server=server)
        finally:
            server.stop()
    finally:
        fake_as.stop()


async def _json_request(
    method: str, url: str, payload: dict[str, object] | None = None
) -> dict[str, object]:
    """Call a local test-control endpoint and decode its JSON object response."""
    headers: dict[str, str] = {}
    content: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(payload).encode("utf-8")
    async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
        response = await client.request(method, url, headers=headers, content=content)
        body = await response.aread()
    assert response.status_code == 200, body.decode("utf-8", errors="replace")
    decoded = json.loads(body)
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


async def _mint_token(
    topology: _Topology,
    *,
    audience: str | None = None,
    issuer: str | None = None,
    scope: str | None = _REQUIRED_SCOPE,
    expires_in: int = 300,
) -> str:
    """Mint a controlled JWT from the fake AS for negative resource-server tests."""
    body = await _json_request(
        "POST",
        f"{topology.issuer}/__test__/mint",
        {
            "audience": audience or f"{topology.server_url}/",
            "issuer": issuer or topology.issuer,
            "scope": scope,
            "expires_in": expires_in,
        },
    )
    token = body.get("access_token")
    assert isinstance(token, str)
    return token


async def _protected_request(topology: _Topology, token: str) -> tuple[int, str]:
    """Send a bearer-authenticated request far enough to exercise the server auth middleware."""
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
    return response.status_code, response.headers.get("WWW-Authenticate", "")


def _modern_tool_request(protocol_version: str = _PROTOCOL_VERSION) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "whoami",
            "arguments": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": protocol_version,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "envelope-integrity-e2e",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


async def _mcp_request(
    topology: _Topology,
    token: str,
    request: dict[str, object],
    *,
    protocol_version: str = _PROTOCOL_VERSION,
    method: str = "tools/call",
    name: str = "whoami",
    session_id: str | None = None,
) -> httpx2.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "MCP-Protocol-Version": protocol_version,
        "Mcp-Method": method,
        "Mcp-Name": name,
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    async with httpx2.AsyncClient(follow_redirects=False, timeout=5.0) as client:
        response = await client.request(
            "POST",
            f"{topology.server_url}/mcp",
            headers=headers,
            json=request,
        )
        await response.aread()
    return response


async def test_full_oauth_flow_reaches_whoami_over_mcp_2026(topology: _Topology) -> None:
    """Exercise the backwards-compatible DCR fallback through an authenticated tool call."""
    settings = Settings(
        auth_provider="generic",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
    )
    storage = InMemoryTokenStorage()
    browser = _AuthorizationRedirectHarness()
    provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=browser.redirect,
        callback_handler=browser.callback,
    )

    async with (
        httpx2.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        assert client.protocol_version == _PROTOCOL_VERSION
        whoami = await client.call_tool("whoami")

    assert whoami.structured_content is not None
    assert whoami.structured_content["authenticated"] is True
    assert whoami.structured_content["client_id"] == "mcp-e2e-client"
    assert whoami.structured_content["subject"] == "e2e-user"
    assert whoami.structured_content["scopes"] == [_REQUIRED_SCOPE]
    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.issuer == topology.issuer
    tokens = await storage.get_tokens()
    assert tokens is not None
    assert tokens.access_token

    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state == {
        "registrations": 1,
        "authorizations": 1,
        "token_exchanges": 1,
        "authorization_scopes": [_REQUIRED_SCOPE],
        "client_credentials_exchanges": 0,
        "client_credentials_scopes": [],
    }


async def test_cimd_first_flow_skips_dynamic_client_registration(
    topology: _Topology,
) -> None:
    """Use the metadata-document URL as client ID when the AS advertises CIMD."""
    await _json_request(
        "POST",
        f"{topology.issuer}/__test__/configure",
        {"client_id_metadata_document_supported": True},
    )
    settings = Settings(
        auth_provider="generic",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
        generic_client_metadata_url=_CIMD_CLIENT_ID,
    )
    storage = InMemoryTokenStorage()
    browser = _AuthorizationRedirectHarness()
    provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=browser.redirect,
        callback_handler=browser.callback,
    )

    async with (
        httpx2.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        assert client.protocol_version == _PROTOCOL_VERSION
        whoami = await client.call_tool("whoami")

    assert whoami.structured_content is not None
    assert whoami.structured_content["authenticated"] is True
    assert whoami.structured_content["client_id"] == _CIMD_CLIENT_ID
    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == _CIMD_CLIENT_ID
    assert client_info.client_secret is None
    assert client_info.token_endpoint_auth_method == "none"
    assert client_info.issuer == topology.issuer

    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state == {
        "registrations": 0,
        "authorizations": 1,
        "token_exchanges": 1,
        "authorization_scopes": [_REQUIRED_SCOPE],
        "client_credentials_exchanges": 0,
        "client_credentials_scopes": [],
    }


async def test_runtime_scope_step_up_preserves_prior_grant_and_completes_health(
    topology: _Topology,
) -> None:
    """Reauthorize once with the prior+challenged scope union and complete the tool call."""
    settings = Settings(
        auth_provider="generic",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
    )
    storage = InMemoryTokenStorage()
    browser = _AuthorizationRedirectHarness()
    provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=browser.redirect,
        callback_handler=browser.callback,
    )

    async with (
        httpx2.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        initial_identity = await client.call_tool("whoami")
        health = await client.call_tool("health")
        elevated_identity = await client.call_tool("whoami")

    assert initial_identity.structured_content is not None
    assert initial_identity.structured_content["scopes"] == [_REQUIRED_SCOPE]
    assert health.structured_content == {"status": "ok"}
    assert elevated_identity.structured_content is not None
    assert elevated_identity.structured_content["scopes"] == [_REQUIRED_SCOPE, _HEALTH_SCOPE]

    tokens = await storage.get_tokens()
    assert tokens is not None
    expected_union = f"{_REQUIRED_SCOPE} {_HEALTH_SCOPE}"
    assert tokens.scope == expected_union

    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state == {
        "registrations": 1,
        "authorizations": 2,
        "token_exchanges": 2,
        "authorization_scopes": [_REQUIRED_SCOPE, expected_union],
        "client_credentials_exchanges": 0,
        "client_credentials_scopes": [],
    }


async def test_client_credentials_flow_is_non_interactive_and_steps_up_scopes(
    topology: _Topology,
) -> None:
    """Use fixed machine credentials without browser, CIMD, or dynamic registration."""
    settings = Settings(
        auth_provider="generic",
        auth_mode="client_credentials",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
        client_credentials_client_id=_MACHINE_CLIENT_ID,
        client_credentials_secret=_MACHINE_CLIENT_CREDENTIAL,
    )
    storage = InMemoryTokenStorage()
    provider = await build_oauth_provider(settings, storage=storage)

    async with (
        httpx2.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        assert client.protocol_version == _PROTOCOL_VERSION
        assert client.server_capabilities.extensions == {OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID: {}}
        initial_identity = await client.call_tool("whoami")
        health = await client.call_tool("health")
        elevated_identity = await client.call_tool("whoami")

    assert initial_identity.structured_content is not None
    assert initial_identity.structured_content == {
        "authenticated": True,
        "client_id": _MACHINE_CLIENT_ID,
        "subject": _MACHINE_CLIENT_ID,
        "scopes": [_REQUIRED_SCOPE],
    }
    assert health.structured_content == {"status": "ok"}
    assert elevated_identity.structured_content is not None
    assert elevated_identity.structured_content["scopes"] == [_REQUIRED_SCOPE, _HEALTH_SCOPE]

    tokens = await storage.get_tokens()
    assert tokens is not None
    expected_union = f"{_REQUIRED_SCOPE} {_HEALTH_SCOPE}"
    assert tokens.scope == expected_union
    assert await storage.get_client_info() is None

    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state == {
        "registrations": 0,
        "authorizations": 0,
        "token_exchanges": 2,
        "authorization_scopes": [],
        "client_credentials_exchanges": 2,
        "client_credentials_scopes": [_REQUIRED_SCOPE, expected_union],
    }


async def test_client_credentials_rejects_an_invalid_secret_without_leaking_it(
    topology: _Topology,
) -> None:
    """Fail closed at the token endpoint and keep the configured credential out of errors."""
    invalid_credential = "invalid-e2e-credential"
    settings = Settings(
        auth_provider="generic",
        auth_mode="client_credentials",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
        client_credentials_client_id=_MACHINE_CLIENT_ID,
        client_credentials_secret=invalid_credential,
    )
    provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())

    with pytest.RaisesGroup(OAuthFlowError) as raised:
        async with (
            httpx2.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http_client,
            build_mcp_client(settings, http_client=http_client),
        ):
            pytest.fail("invalid client credentials unexpectedly connected")

    assert invalid_credential not in str(raised.value)
    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state == {
        "registrations": 0,
        "authorizations": 0,
        "token_exchanges": 0,
        "authorization_scopes": [],
        "client_credentials_exchanges": 0,
        "client_credentials_scopes": [],
    }


async def test_modern_request_envelope_integrity_and_sessionless_transport(
    topology: _Topology,
) -> None:
    """Exercise the modern routing-header, version, and sessionless boundary."""
    token = await _mint_token(topology, scope=f"{_REQUIRED_SCOPE} {_HEALTH_SCOPE}")
    accepted = await _mcp_request(
        topology,
        token,
        _modern_tool_request(),
        session_id="legacy-session-id-must-not-create-state",
    )

    assert accepted.status_code == 200
    assert "mcp-session-id" not in accepted.headers
    accepted_payload = cast(dict[str, object], accepted.json())
    result = cast(dict[str, object], accepted_payload["result"])
    structured_content = cast(dict[str, object], result["structuredContent"])
    assert structured_content["authenticated"] is True

    for mismatched_headers in (
        {"method": "tools/list"},
        {"name": "health"},
    ):
        rejected = await _mcp_request(
            topology,
            token,
            _modern_tool_request(),
            **mismatched_headers,
        )
        assert rejected.status_code == 400
        rejected_payload = cast(dict[str, object], rejected.json())
        error = cast(dict[str, object], rejected_payload["error"])
        assert error["code"] == _HEADER_MISMATCH

    requested_version = "2099-01-01"
    unsupported = await _mcp_request(
        topology,
        token,
        _modern_tool_request(requested_version),
        protocol_version=requested_version,
    )
    assert unsupported.status_code == 400
    unsupported_payload = cast(dict[str, object], unsupported.json())
    error = cast(dict[str, object], unsupported_payload["error"])
    assert error["code"] == _UNSUPPORTED_PROTOCOL_VERSION
    assert error["data"] == {
        "supported": [_PROTOCOL_VERSION],
        "requested": requested_version,
    }


async def test_changed_authorization_server_discards_bound_registration(
    topology: _Topology,
) -> None:
    """Discard a persisted registration bound to another AS and complete DCR against this AS."""
    settings = Settings(
        auth_provider="generic",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
    )
    storage = InMemoryTokenStorage()
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="stale-client",
            redirect_uris=[AnyUrl(_CALLBACK_URI)],
            token_endpoint_auth_method="none",
            issuer="https://old-as.example.invalid",
        )
    )
    browser = _AuthorizationRedirectHarness()
    provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=browser.redirect,
        callback_handler=browser.callback,
    )

    async with (
        httpx2.AsyncClient(auth=provider, follow_redirects=True, timeout=30.0) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        result = await client.call_tool("whoami")

    assert result.structured_content is not None
    replacement = await storage.get_client_info()
    assert replacement is not None
    assert replacement.client_id == "mcp-e2e-client"
    assert replacement.issuer == topology.issuer
    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state["registrations"] == 1


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        ({"audience": "https://wrong-resource.example.invalid/"}, 401),
        ({"issuer": "https://wrong-issuer.example.invalid"}, 401),
        ({"expires_in": -60}, 401),
    ],
    ids=["wrong-audience", "wrong-issuer", "expired"],
)
async def test_invalid_tokens_fail_closed(
    topology: _Topology,
    overrides: dict[str, object],
    expected_status: int,
) -> None:
    """Reject invalid bearer tokens before MCP request processing."""
    audience = overrides.get("audience")
    issuer = overrides.get("issuer")
    expires_in = overrides.get("expires_in")
    token = await _mint_token(
        topology,
        audience=audience if isinstance(audience, str) else None,
        issuer=issuer if isinstance(issuer, str) else None,
        expires_in=expires_in if isinstance(expires_in, int) else 300,
    )

    status, _ = await _protected_request(topology, token)

    assert status == expected_status


async def test_insufficient_scope_returns_step_up_challenge(topology: _Topology) -> None:
    """Return a standards-shaped 403 challenge containing the scope the client must request."""
    token = await _mint_token(topology, scope="mcp:tools:list")

    status, challenge = await _protected_request(topology, token)

    assert status == 403
    assert "insufficient_scope" in challenge
    assert _REQUIRED_SCOPE in challenge


async def test_rfc9207_authorization_response_issuer_mismatch_is_rejected(
    topology: _Topology,
) -> None:
    """Reject a code callback whose RFC 9207 ``iss`` does not match discovered AS metadata."""
    await _json_request(
        "POST",
        f"{topology.issuer}/__test__/configure",
        {"authorization_response_iss": "https://wrong-issuer.example.invalid"},
    )
    settings = Settings(
        auth_provider="generic",
        server_url=topology.server_url,
        token_storage_path=None,
        oauth_allow_insecure_loopback=True,
    )
    storage = InMemoryTokenStorage()
    browser = _AuthorizationRedirectHarness()
    provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=browser.redirect,
        callback_handler=browser.callback,
    )

    try:
        async with httpx2.AsyncClient(
            auth=provider, follow_redirects=False, timeout=10.0
        ) as client:
            with pytest.raises(OAuthFlowError, match="iss mismatch"):
                response = await client.request(
                    "POST",
                    f"{topology.server_url}/mcp",
                    headers={"Content-Type": "application/json"},
                    content=b"{}",
                )
                await response.aread()
    finally:
        await _json_request(
            "POST",
            f"{topology.issuer}/__test__/configure",
            {"authorization_response_iss": None},
        )

    state = await _json_request("GET", f"{topology.issuer}/__test__/state")
    assert state["authorizations"] == 1
    assert state["token_exchanges"] == 0
