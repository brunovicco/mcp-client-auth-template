"""Security tests for SSRF-resistant OAuth discovery egress."""

import ipaddress
from collections.abc import AsyncIterator, Sequence

import httpx2
import pytest

from mcp_client_auth_template.adapters.oauth_discovery_security import (
    OAuthDiscoverySecurityPolicy,
    OAuthNetworkSecurityError,
    PinnedDnsAsyncTransport,
    Resolver,
)
from mcp_client_auth_template.entrypoints.settings import Settings

_PUBLIC_IP = ipaddress.ip_address("1.1.1.1")
_OTHER_PUBLIC_IP = ipaddress.ip_address("8.8.8.8")
_PRIVATE_IP = ipaddress.ip_address("10.0.0.10")
_LOOPBACK_IP = ipaddress.ip_address("127.0.0.1")
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


async def _public_resolver(_host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
    return (_PUBLIC_IP,)


async def _private_resolver(_host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
    return (_PRIVATE_IP,)


async def _loopback_resolver(_host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
    return (_LOOPBACK_IP,)


async def _mixed_resolver(_host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
    return (_PUBLIC_IP, _PRIVATE_IP)


def _policy(
    *,
    allow_insecure_loopback: bool = False,
    resolver: Resolver = _public_resolver,
    max_oauth_response_bytes: int = 1024,
) -> OAuthDiscoverySecurityPolicy:
    return OAuthDiscoverySecurityPolicy(
        resource_server_url="https://mcp.example.com",
        allow_insecure_loopback=allow_insecure_loopback,
        resolver=resolver,
        max_oauth_response_bytes=max_oauth_response_bytes,
    )


async def test_production_requires_https() -> None:
    with pytest.raises(OAuthNetworkSecurityError, match="HTTPS is required"):
        await _policy().resolve("http://auth.example.com/.well-known/openid-configuration")


async def test_production_rejects_private_dns_answers() -> None:
    with pytest.raises(OAuthNetworkSecurityError, match="private, loopback"):
        await _policy(resolver=_private_resolver).resolve(
            "https://auth.example.com/.well-known/openid-configuration"
        )


async def test_production_rejects_loopback_even_over_https() -> None:
    with pytest.raises(OAuthNetworkSecurityError, match="private, loopback"):
        await _policy(resolver=_loopback_resolver).resolve(
            "https://auth.example.com/.well-known/openid-configuration"
        )


async def test_explicit_development_escape_allows_only_loopback() -> None:
    target = await _policy(
        allow_insecure_loopback=True,
        resolver=_loopback_resolver,
    ).resolve("http://localhost:8080/.well-known/openid-configuration")

    assert target.ip == _LOOPBACK_IP
    assert target.is_loopback is True


async def test_development_escape_does_not_allow_private_lan() -> None:
    with pytest.raises(OAuthNetworkSecurityError, match="HTTPS is required"):
        await _policy(
            allow_insecure_loopback=True,
            resolver=_private_resolver,
        ).resolve("http://auth.internal/.well-known/openid-configuration")


async def test_mixed_public_and_private_dns_answers_fail_closed() -> None:
    with pytest.raises(OAuthNetworkSecurityError, match="private, loopback"):
        await _policy(resolver=_mixed_resolver).resolve(
            "https://auth.example.com/.well-known/openid-configuration"
        )


async def test_ipv4_mapped_private_ipv6_literal_is_rejected() -> None:
    with pytest.raises(OAuthNetworkSecurityError, match="private, loopback"):
        await _policy().resolve("https://[::ffff:10.0.0.1]/.well-known/openid-configuration")


async def test_literal_cloud_metadata_address_is_rejected_without_dns() -> None:
    with pytest.raises(OAuthNetworkSecurityError):
        await _policy().resolve("http://169.254.169.254/latest/meta-data")


async def test_transport_connects_to_validated_ip_and_preserves_host_and_sni() -> None:
    observed: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(200, json={"issuer": "https://auth.example.com"})

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        request = httpx2.Request("GET", "https://auth.example.com/.well-known/openid-configuration")
        response = await transport.handle_async_request(request)
        await response.aread()
    finally:
        await transport.aclose()

    assert len(observed) == 1
    network_request = observed[0]
    assert network_request.url.host == str(_PUBLIC_IP)
    assert network_request.headers["Host"] == "auth.example.com"
    assert network_request.extensions["sni_hostname"] == "auth.example.com"
    assert network_request.extensions["timeout"]["read"] == 10.0
    assert response.request.url.host == "auth.example.com"


async def test_redirect_target_is_validated_before_it_can_be_followed() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        request = httpx2.Request("GET", "https://auth.example.com/.well-known/openid-configuration")
        with pytest.raises(OAuthNetworkSecurityError):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()


async def test_safe_redirects_are_revalidated_hop_by_hop() -> None:
    resolved: list[str] = []

    async def resolver(host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
        resolved.append(host)
        return (_PUBLIC_IP,)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx2.Response(302, headers={"Location": "/oauth/metadata"})
        return httpx2.Response(200, json={"issuer": "https://auth.example.com"})

    transport = PinnedDnsAsyncTransport(
        policy=_policy(resolver=resolver),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        async with httpx2.AsyncClient(transport=transport, follow_redirects=True) as client:
            response = await client.get("https://auth.example.com/.well-known/openid-configuration")
            assert response.status_code == 200
    finally:
        await transport.aclose()

    assert resolved.count("auth.example.com") >= 2


async def test_cross_origin_redirect_is_rejected_even_when_target_is_public() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            302, headers={"Location": "https://other.example.net/oauth/metadata"}
        )

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        request = httpx2.Request("GET", "https://auth.example.com/.well-known/openid-configuration")
        with pytest.raises(OAuthNetworkSecurityError, match="cross-origin"):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()


async def test_oauth_control_plane_content_length_is_bounded() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"Content-Length": "2048"}, content=b"")

    transport = PinnedDnsAsyncTransport(
        policy=_policy(max_oauth_response_bytes=1024),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        request = httpx2.Request("GET", "https://auth.example.com/.well-known/openid-configuration")
        with pytest.raises(OAuthNetworkSecurityError, match="size limit"):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()


class _ChunkStream(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"a" * 700
        yield b"b" * 700

    async def aclose(self) -> None:
        return None


async def test_chunked_oauth_response_cannot_bypass_size_limit() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=_ChunkStream())

    transport = PinnedDnsAsyncTransport(
        policy=_policy(max_oauth_response_bytes=1024),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        request = httpx2.Request("GET", "https://auth.example.com/.well-known/openid-configuration")
        response = await transport.handle_async_request(request)
        with pytest.raises(OAuthNetworkSecurityError, match="size limit"):
            await response.aread()
    finally:
        await transport.aclose()


async def test_compressed_oauth_control_plane_response_is_rejected() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=_ChunkStream())

    transport = PinnedDnsAsyncTransport(
        policy=_policy(),
        transport_factory=lambda: httpx2.MockTransport(handler),
    )
    try:
        request = httpx2.Request("GET", "https://auth.example.com/.well-known/openid-configuration")
        with pytest.raises(OAuthNetworkSecurityError, match="compressed"):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()


async def test_browser_redirect_is_validated_before_handler_runs() -> None:
    called = False

    async def handler(_url: str) -> None:
        nonlocal called
        called = True

    guarded = _policy(resolver=_private_resolver).wrap_browser_redirect(handler)
    with pytest.raises(OAuthNetworkSecurityError):
        await guarded("https://auth.example.com/authorize?client_id=abc")

    assert called is False


def test_settings_declare_fail_closed_defaults() -> None:
    fields = Settings.model_fields

    assert fields["oauth_allow_insecure_loopback"].default is False
    assert fields["oauth_dns_timeout_seconds"].default == 2.0
    assert fields["oauth_http_timeout_seconds"].default == 10.0
    assert fields["oauth_max_response_bytes"].default == 1_048_576
    assert fields["oauth_max_redirects"].default == 5
    assert fields["oauth_max_hosts"].default == 16


def test_settings_allow_explicit_loopback_development_escape() -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="http://localhost:8000",
        oauth_allow_insecure_loopback=True,
    )

    assert settings.oauth_allow_insecure_loopback is True


def test_same_origin_embedded_token_endpoint_is_control_plane() -> None:
    policy = _policy()

    assert policy.is_oauth_control_plane("https://mcp.example.com/token") is True
    assert policy.is_oauth_control_plane("https://mcp.example.com/register") is True
    assert policy.is_oauth_control_plane("https://mcp.example.com/mcp") is False


async def test_outbound_host_budget_is_enforced() -> None:
    async def resolver(_host: str, _port: int, _timeout: float) -> Sequence[IPAddress]:
        return (_PUBLIC_IP,)

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={})

    transport = PinnedDnsAsyncTransport(
        policy=_policy(resolver=resolver),
        transport_factory=lambda: httpx2.MockTransport(handler),
        max_hosts=1,
    )
    try:
        first = await transport.handle_async_request(
            httpx2.Request("GET", "https://one.example.com/.well-known/openid-configuration")
        )
        await first.aread()
        with pytest.raises(OAuthNetworkSecurityError, match="host budget"):
            await transport.handle_async_request(
                httpx2.Request("GET", "https://two.example.com/.well-known/openid-configuration")
            )
    finally:
        await transport.aclose()
