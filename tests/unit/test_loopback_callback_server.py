"""Socket-level tests for the hardened RFC 8252 loopback callback listener."""

import anyio
import httpx2
import pytest
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.loopback_callback_server import LoopbackCallbackServer


async def _noop_redirect(_url: str) -> None:
    return None


async def _arm(server: LoopbackCallbackServer, state: str = "abc123") -> None:
    handler = server.wrap_redirect_handler(_noop_redirect)
    await handler(
        "https://as.example.invalid/authorize"
        f"?response_type=code&state={state}&redirect_uri={server.redirect_uri}"
    )


async def _run_success(
    server: LoopbackCallbackServer,
    *,
    state: str = "abc123",
    iss: str | None = None,
) -> AuthorizationCodeResult:
    await _arm(server, state)
    outcomes: list[AuthorizationCodeResult] = []
    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        params = {"code": "auth-code", "state": state}
        if iss is not None:
            params["iss"] = iss
        response = await http_client.get(server.redirect_uri, params=params)
        assert response.status_code == 200
    return outcomes[0]


async def test_returns_code_state_and_issuer_from_valid_callback() -> None:
    server = LoopbackCallbackServer(port=18765, timeout_seconds=5)

    result = await _run_success(server, iss="https://as.example.invalid")

    assert result.code == "auth-code"
    assert result.state == "abc123"
    assert result.iss == "https://as.example.invalid"


async def test_valid_authorization_error_is_terminal_only_with_exact_state() -> None:
    server = LoopbackCallbackServer(port=18766, timeout_seconds=5)
    await _arm(server)

    async def send_error_callback() -> None:
        await anyio.sleep(0.2)
        async with httpx2.AsyncClient() as http_client:
            response = await http_client.get(
                server.redirect_uri,
                params={
                    "error": "access_denied",
                    "error_description": "user declined",
                    "state": "abc123",
                },
            )
            assert response.status_code == 400

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_error_callback)
        with pytest.raises(OAuthFlowError, match="OAuth error"):
            await server.wait_for_callback()


async def test_wrong_path_does_not_consume_legitimate_callback() -> None:
    server = LoopbackCallbackServer(port=18767, timeout_seconds=5)
    await _arm(server)
    outcomes: list[AuthorizationCodeResult] = []

    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        wrong = await http_client.get("http://127.0.0.1:18767/not-callback")
        assert wrong.status_code == 404
        valid = await http_client.get(
            server.redirect_uri, params={"code": "auth-code", "state": "abc123"}
        )
        assert valid.status_code == 200

    assert outcomes[0].code == "auth-code"


async def test_favicon_probe_does_not_consume_legitimate_callback() -> None:
    server = LoopbackCallbackServer(port=18768, timeout_seconds=5)
    await _arm(server)
    outcomes: list[AuthorizationCodeResult] = []

    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        favicon = await http_client.get("http://127.0.0.1:18768/favicon.ico")
        assert favicon.status_code == 204
        await http_client.get(server.redirect_uri, params={"code": "auth-code", "state": "abc123"})

    assert outcomes[0].state == "abc123"


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://127.0.0.1:18769/callback?code=one&code=two&state=abc123",
        "http://127.0.0.1:18769/callback?code=one&state=abc123&state=abc123",
        "http://127.0.0.1:18769/callback?code=one&state=wrong",
        "http://127.0.0.1:18769/callback?state=abc123",
        "http://127.0.0.1:18769/callback?code=one&state=abc123&iss=one&iss=two",
        "http://127.0.0.1:18769/callback?code=one&state=abc123&error=access_denied",
        "http://127.0.0.1:18769/callback?code=one&state=abc123&iss=",
        "http://127.0.0.1:18769/callback?code=one&state=abc123&broken",
        "http://127.0.0.1:18769/callback?code=%ZZ&state=abc123",
    ],
)
async def test_malformed_or_wrong_state_request_is_rejected_and_listener_continues(
    invalid_url: str,
) -> None:
    server = LoopbackCallbackServer(port=18769, timeout_seconds=5)
    await _arm(server)
    outcomes: list[AuthorizationCodeResult] = []

    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        invalid = await http_client.get(invalid_url)
        assert invalid.status_code == 400
        valid = await http_client.get(
            server.redirect_uri, params={"code": "auth-code", "state": "abc123"}
        )
        assert valid.status_code == 200

    assert outcomes[0].code == "auth-code"


async def test_wrong_host_header_is_rejected_and_listener_continues() -> None:
    server = LoopbackCallbackServer(port=18770, timeout_seconds=5)
    await _arm(server)
    outcomes: list[AuthorizationCodeResult] = []

    async with anyio.create_task_group() as tg, httpx2.AsyncClient() as http_client:

        async def wait() -> None:
            outcomes.append(await server.wait_for_callback())

        tg.start_soon(wait)
        await anyio.sleep(0.2)
        invalid = await http_client.get(
            server.redirect_uri,
            params={"code": "auth-code", "state": "abc123"},
            headers={"Host": "evil.example"},
        )
        assert invalid.status_code == 400
        await http_client.get(server.redirect_uri, params={"code": "auth-code", "state": "abc123"})

    assert outcomes[0].code == "auth-code"


async def test_timeout_is_global_and_listener_must_be_armed() -> None:
    server = LoopbackCallbackServer(port=18771, timeout_seconds=0.2)

    with pytest.raises(OAuthFlowError, match="not armed"):
        await server.wait_for_callback()

    await _arm(server)
    with pytest.raises(OAuthFlowError, match="timed out"):
        await server.wait_for_callback()


async def test_request_budget_bounds_invalid_request_spam() -> None:
    server = LoopbackCallbackServer(port=18772, timeout_seconds=5, max_requests=2)
    await _arm(server)

    async def send_invalid_requests() -> None:
        await anyio.sleep(0.2)
        async with httpx2.AsyncClient() as http_client:
            assert (await http_client.get("http://127.0.0.1:18772/nope-1")).status_code == 404
            assert (await http_client.get("http://127.0.0.1:18772/nope-2")).status_code == 404

    async with anyio.create_task_group() as tg:
        tg.start_soon(send_invalid_requests)
        with pytest.raises(OAuthFlowError, match="request limit exceeded"):
            await server.wait_for_callback()


@pytest.mark.parametrize(
    "host",
    ["localhost", "0.0.0.0", "192.168.1.10", "example.com"],  # noqa: S104
)
def test_non_loopback_or_hostname_bind_targets_are_rejected(host: str) -> None:
    with pytest.raises(ValueError, match="loopback callback host"):
        LoopbackCallbackServer(host=host)


def test_ipv6_redirect_uri_uses_required_brackets() -> None:
    server = LoopbackCallbackServer(host="::1", port=9999, path="/oauth/callback")

    assert server.redirect_uri == "http://[::1]:9999/oauth/callback"


@pytest.mark.parametrize("path", ["callback", "//callback", "/callback?x=1", "/callback#frag"])
def test_invalid_callback_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="absolute URL path"):
        LoopbackCallbackServer(path=path)


async def test_redirect_wrapper_requires_exact_redirect_uri_and_one_nonempty_state() -> None:
    server = LoopbackCallbackServer(port=18773)
    handler = server.wrap_redirect_handler(_noop_redirect)

    with pytest.raises(OAuthFlowError, match="state"):
        await handler(
            "https://as.example.invalid/authorize?redirect_uri=http://127.0.0.1:18773/callback"
        )
    with pytest.raises(OAuthFlowError, match="redirect_uri"):
        await handler(
            "https://as.example.invalid/authorize"
            "?state=abc123&redirect_uri=http://127.0.0.1:9999/callback"
        )
