"""Unit tests for :class:`LoopbackCallbackServer`.

Each test binds a real socket on ``127.0.0.1`` (distinct ports per test to
avoid TIME_WAIT collisions) and drives it with a real HTTP request - this is
the one piece of the client template that talks to an actual socket, so it
is worth testing against the real thing rather than a fake.
"""

import anyio
import httpx2
import pytest
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.loopback_callback_server import LoopbackCallbackServer


async def test_returns_the_code_and_state_from_a_successful_callback() -> None:
    server = LoopbackCallbackServer(port=18765, timeout_seconds=5)
    outcomes: list[AuthorizationCodeResult] = []

    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        response = await http_client.get(
            server.redirect_uri, params={"code": "auth-code", "state": "abc123"}
        )
        assert response.status_code == 200

    assert outcomes[0].code == "auth-code"
    assert outcomes[0].state == "abc123"


async def test_captures_the_issuer_when_present() -> None:
    server = LoopbackCallbackServer(port=18766, timeout_seconds=5)
    outcomes: list[AuthorizationCodeResult] = []

    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        await http_client.get(
            server.redirect_uri,
            params={"code": "auth-code", "state": "abc123", "iss": "https://as.example.invalid"},
        )

    assert outcomes[0].iss == "https://as.example.invalid"


async def test_raises_on_an_authorization_server_error() -> None:
    server = LoopbackCallbackServer(port=18767, timeout_seconds=5)

    async def send_error_callback() -> None:
        await anyio.sleep(0.2)
        async with httpx2.AsyncClient() as http_client:
            await http_client.get(
                server.redirect_uri,
                params={"error": "access_denied", "error_description": "user declined"},
            )

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_error_callback)
        with pytest.raises(OAuthFlowError, match="user declined"):
            await server.wait_for_callback()


async def test_raises_on_timeout_with_no_callback() -> None:
    server = LoopbackCallbackServer(port=18768, timeout_seconds=0.5)

    with pytest.raises(OAuthFlowError, match="timed out"):
        await server.wait_for_callback()


async def test_raises_on_a_callback_with_neither_code_nor_error() -> None:
    server = LoopbackCallbackServer(port=18769, timeout_seconds=5)

    async def send_malformed_callback() -> None:
        await anyio.sleep(0.2)
        async with httpx2.AsyncClient() as http_client:
            response = await http_client.get(server.redirect_uri, params={"state": "abc123"})
            assert response.status_code == 400

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_malformed_callback)
        with pytest.raises(OAuthFlowError, match="had no 'code' or 'error' parameter"):
            await server.wait_for_callback()


async def test_a_request_to_the_wrong_path_gets_a_404_and_the_callback_still_times_out() -> None:
    wrong_path_url = "http://127.0.0.1:18770/not-the-callback-path"
    server = LoopbackCallbackServer(port=18770, timeout_seconds=5)

    async def hit_the_wrong_path() -> None:
        await anyio.sleep(0.2)
        async with httpx2.AsyncClient() as http_client:
            response = await http_client.get(wrong_path_url)
            assert response.status_code == 404

    async with anyio.create_task_group() as tg:
        tg.start_soon(hit_the_wrong_path)
        with pytest.raises(OAuthFlowError, match="timed out"):
            await server.wait_for_callback()


def test_redirect_uri_reflects_host_port_and_path() -> None:
    server = LoopbackCallbackServer(host="127.0.0.1", port=9999, path="/oauth/callback")

    assert server.redirect_uri == "http://127.0.0.1:9999/oauth/callback"
