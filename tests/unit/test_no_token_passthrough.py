"""Bearer credentials must never leave the configured MCP resource endpoint."""

import ipaddress
from collections.abc import Sequence

import httpx2
import pytest

from mcp_client_auth_template.adapters.oauth_discovery_security import (
    OAuthDiscoverySecurityPolicy,
    OAuthNetworkSecurityError,
    PinnedDnsAsyncTransport,
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_PUBLIC_IP = ipaddress.ip_address("1.1.1.1")


async def _resolver(_host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
    return (_PUBLIC_IP,)


def _policy() -> OAuthDiscoverySecurityPolicy:
    return OAuthDiscoverySecurityPolicy(
        resource_server_url="https://mcp.example.invalid",
        resolver=_resolver,
    )


async def test_bearer_is_allowed_only_on_exact_mcp_endpoint() -> None:
    observed: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(200, json={"ok": True})

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    request = httpx2.Request(
        "POST",
        "https://mcp.example.invalid/mcp",
        headers={"Authorization": "Bearer resource-token"},
    )

    response = await transport.handle_async_request(request)
    await response.aread()

    assert len(observed) == 1
    assert observed[0].headers["Authorization"] == "Bearer resource-token"


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.example.invalid/token",
        "https://mcp.example.invalid/.well-known/oauth-protected-resource",
        "https://mcp.example.invalid/other",
        "https://mcp.example.invalid/mcp?unexpected=1",
    ],
)
async def test_bearer_to_oauth_or_unexpected_target_is_blocked_before_network_io(url: str) -> None:
    called = False

    async def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200)

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    request = httpx2.Request("POST", url, headers={"Authorization": "bEaReR secret-token"})

    with pytest.raises(OAuthNetworkSecurityError, match="only be sent"):
        await transport.handle_async_request(request)

    assert called is False


async def test_non_bearer_authorization_is_not_treated_as_mcp_token_passthrough() -> None:
    observed: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(200, json={"ok": True})

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    request = httpx2.Request(
        "POST",
        "https://auth.example.invalid/token",
        headers={"Authorization": "Basic client-credential"},
    )

    response = await transport.handle_async_request(request)
    await response.aread()

    assert len(observed) == 1
