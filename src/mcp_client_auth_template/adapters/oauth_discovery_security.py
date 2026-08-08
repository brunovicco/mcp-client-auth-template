"""SSRF-resistant outbound transport for MCP OAuth discovery and token traffic.

MCP OAuth clients consume URLs controlled by the remote MCP server and authorization
server: protected-resource metadata, authorization-server metadata, registration and
token endpoints. This module puts one network policy below the SDK so every actual
HTTP request, including automatically-followed redirects, crosses the same checks.

The transport resolves hostnames itself, rejects any non-global address in production,
and rewrites the network request to the validated IP while preserving the original Host
header and TLS SNI hostname. The underlying HTTP transport therefore cannot perform a
second DNS lookup between validation and connect (DNS-rebinding/TOCTOU defense).
"""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin, urlsplit

import httpx2

from mcp_client_auth_template.adapters.security_audit import (
    ClientSecurityAuditAction,
    ClientSecurityAuditOutcome,
    emit_client_security_audit,
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int, float], Awaitable[Sequence[IPAddress]]]
TransportFactory = Callable[[], httpx2.AsyncBaseTransport]

_WELL_KNOWN_MARKERS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
)


class OAuthNetworkSecurityError(RuntimeError):
    """Raised when an outbound OAuth/MCP request violates the network trust policy."""


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """One validated URL target and the IP that the transport must connect to."""

    scheme: str
    host: str
    port: int
    ip: IPAddress
    is_loopback: bool


async def _system_resolver(host: str, port: int, timeout_seconds: float) -> Sequence[IPAddress]:
    """Resolve ``host`` with a bounded asynchronous getaddrinfo call."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return (literal,)

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise OAuthNetworkSecurityError("DNS resolution timed out") from exc
    except socket.gaierror as exc:
        raise OAuthNetworkSecurityError("DNS resolution failed") from exc

    addresses: list[IPAddress] = []
    seen: set[IPAddress] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        raw_address = str(sockaddr[0]).split("%", maxsplit=1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:  # pragma: no cover - OS resolver contract violation
            raise OAuthNetworkSecurityError("DNS resolver returned an invalid IP address") from exc
        if address not in seen:
            addresses.append(address)
            seen.add(address)
    if not addresses:
        raise OAuthNetworkSecurityError("DNS resolution returned no usable addresses")
    return tuple(addresses)


def _effective_address(address: IPAddress) -> IPAddress:
    """Normalize IPv4-mapped IPv6 before private/reserved classification."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _origin(scheme: str, host: str, port: int) -> tuple[str, str, int]:
    return scheme, host.lower().rstrip("."), port


def _default_port(scheme: str) -> int:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    raise OAuthNetworkSecurityError("only HTTP and HTTPS URLs are permitted")


def _host_header(host: str, port: int, scheme: str) -> str:
    """Build the original HTTP authority, preserving IPv6 bracket syntax."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    rendered = f"[{host}]" if isinstance(literal, ipaddress.IPv6Address) else host
    if port != _default_port(scheme):
        rendered = f"{rendered}:{port}"
    return rendered


class OAuthDiscoverySecurityPolicy:
    """Validate outbound OAuth/MCP URLs and resolve them to a safe connect target."""

    def __init__(
        self,
        *,
        resource_server_url: str,
        allow_insecure_loopback: bool = False,
        dns_timeout_seconds: float = 2.0,
        http_timeout_seconds: float = 10.0,
        max_oauth_response_bytes: int = 1_048_576,
        resolver: Resolver | None = None,
    ) -> None:
        """Create a policy rooted at one configured MCP resource-server URL."""
        if dns_timeout_seconds <= 0:
            raise ValueError("dns_timeout_seconds must be positive")
        if http_timeout_seconds <= 0:
            raise ValueError("http_timeout_seconds must be positive")
        if max_oauth_response_bytes <= 0:
            raise ValueError("max_oauth_response_bytes must be positive")

        parsed = urlsplit(resource_server_url)
        if not parsed.hostname:
            raise ValueError("resource_server_url must contain a host")
        scheme = parsed.scheme.lower()
        port = parsed.port or _default_port(scheme)

        self._resource_origin = _origin(scheme, parsed.hostname, port)
        resource_path = parsed.path.rstrip("/")
        self._mcp_path = f"{resource_path}/mcp" if resource_path else "/mcp"
        self._allow_insecure_loopback = allow_insecure_loopback
        self._dns_timeout_seconds = dns_timeout_seconds
        self._http_timeout_seconds = http_timeout_seconds
        self._max_oauth_response_bytes = max_oauth_response_bytes
        self._resolver = resolver or _system_resolver

    @property
    def http_timeout_seconds(self) -> float:
        """Per-operation timeout used for OAuth control-plane HTTP requests."""
        return self._http_timeout_seconds

    @property
    def max_oauth_response_bytes(self) -> int:
        """Maximum raw response size accepted for the OAuth control plane."""
        return self._max_oauth_response_bytes

    async def resolve(self, url: str) -> ResolvedTarget:
        """Validate a URL, resolve all addresses, and choose one safe pinned IP.

        Production policy requires HTTPS and globally-routable IP addresses. The only
        opt-out is ``allow_insecure_loopback`` for explicit local development, where
        HTTP/HTTPS targets are accepted only when *every* resolved address is loopback.
        Mixed public/private DNS answers fail closed.
        """
        try:
            parsed = urlsplit(url)
            port = parsed.port or _default_port(parsed.scheme.lower())
        except ValueError as exc:
            raise OAuthNetworkSecurityError("outbound URL is malformed") from exc

        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise OAuthNetworkSecurityError("only HTTP and HTTPS URLs are permitted")
        if not parsed.hostname:
            raise OAuthNetworkSecurityError("outbound URL must contain a host")
        if parsed.username is not None or parsed.password is not None:
            raise OAuthNetworkSecurityError("userinfo is not permitted in outbound URLs")
        if parsed.fragment:
            raise OAuthNetworkSecurityError("fragments are not permitted in outbound URLs")

        host = parsed.hostname
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        addresses = (
            (literal,)
            if literal is not None
            else tuple(await self._resolver(host, port, self._dns_timeout_seconds))
        )
        if not addresses:
            raise OAuthNetworkSecurityError("DNS resolution returned no usable addresses")

        effective = tuple(_effective_address(address) for address in addresses)
        all_loopback = all(address.is_loopback for address in effective)
        if all_loopback and self._allow_insecure_loopback:
            return ResolvedTarget(
                scheme=scheme, host=host, port=port, ip=addresses[0], is_loopback=True
            )

        if scheme != "https":
            raise OAuthNetworkSecurityError("HTTPS is required for non-loopback outbound URLs")
        if any(not address.is_global for address in effective):
            raise OAuthNetworkSecurityError(
                "DNS resolution included a private, loopback, link-local, reserved, "
                "or non-global address"
            )

        return ResolvedTarget(
            scheme=scheme, host=host, port=port, ip=addresses[0], is_loopback=False
        )

    def is_mcp_resource_request(self, url: str) -> bool:
        """Return whether ``url`` is the exact configured MCP data-plane endpoint."""
        parsed = urlsplit(url)
        if not parsed.hostname:
            return False
        try:
            port = parsed.port or _default_port(parsed.scheme.lower())
        except (ValueError, OAuthNetworkSecurityError):
            return False
        current_origin = _origin(parsed.scheme.lower(), parsed.hostname, port)
        return (
            current_origin == self._resource_origin
            and parsed.path == self._mcp_path
            and not parsed.query
            and not parsed.fragment
        )

    def is_oauth_control_plane(self, url: str) -> bool:
        """Return whether a response should receive the tighter OAuth HTTP policy.

        This client uses one HTTP stack for exactly two kinds of traffic: MCP data-plane
        requests to its configured ``.../mcp`` endpoint, and OAuth control-plane requests.
        Treating every same-origin path except the MCP endpoint as control plane also covers
        embedded authorization servers whose DCR/token endpoints live on the resource origin.
        """
        parsed = urlsplit(url)
        if not parsed.hostname:
            return True
        try:
            port = parsed.port or _default_port(parsed.scheme.lower())
        except (ValueError, OAuthNetworkSecurityError):
            return True
        current_origin = _origin(parsed.scheme.lower(), parsed.hostname, port)
        if current_origin != self._resource_origin:
            return True
        if any(marker in parsed.path for marker in _WELL_KNOWN_MARKERS):
            return True
        return parsed.path != self._mcp_path

    def wrap_browser_redirect(
        self, handler: Callable[[str], Awaitable[None]]
    ) -> Callable[[str], Awaitable[None]]:
        """Apply URL/DNS policy before handing an authorization URL to the system browser."""

        async def guarded_redirect(authorization_url: str) -> None:
            await self.resolve(authorization_url)
            await handler(authorization_url)

        return guarded_redirect


def _has_bearer_authorization(headers: httpx2.Headers) -> bool:
    value = headers.get("Authorization")
    if value is None:
        return False
    parts = value.split(None, maxsplit=1)
    return bool(parts) and parts[0].casefold() == "bearer"


class _BoundedAsyncByteStream(httpx2.AsyncByteStream):
    """Enforce a raw-byte limit while preserving streaming semantics."""

    def __init__(self, stream: httpx2.AsyncByteStream, *, limit: int) -> None:
        """Wrap one async response stream with a raw-byte ceiling."""
        self._stream = stream
        self._limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield chunks until the configured byte ceiling would be exceeded."""
        total = 0
        async for chunk in self._stream:
            total += len(chunk)
            if total > self._limit:
                await self._stream.aclose()
                raise OAuthNetworkSecurityError("OAuth response exceeded the configured size limit")
            yield chunk

    async def aclose(self) -> None:
        """Close the wrapped network stream."""
        await self._stream.aclose()


class PinnedDnsAsyncTransport(httpx2.AsyncBaseTransport):
    """Validate, DNS-pin, and safely forward every outbound HTTP request.

    Each original hostname receives its own child transport so connection pooling can
    never reuse a TLS connection across two hostnames that happen to resolve to the same
    IP. Every request is rewritten to the IP selected by ``policy.resolve`` while the
    original authority remains in the Host header and HTTPS SNI extension.
    """

    def __init__(
        self,
        *,
        policy: OAuthDiscoverySecurityPolicy,
        transport_factory: TransportFactory,
        max_hosts: int = 16,
    ) -> None:
        """Create a DNS-pinning transport with a bounded set of per-host pools."""
        if max_hosts <= 0:
            raise ValueError("max_hosts must be positive")
        self._policy = policy
        self._transport_factory = transport_factory
        self._max_hosts = max_hosts
        self._transports: dict[tuple[str, str, int], httpx2.AsyncBaseTransport] = {}
        self._transport_lock = asyncio.Lock()

    async def _transport_for(self, target: ResolvedTarget) -> httpx2.AsyncBaseTransport:
        key = _origin(target.scheme, target.host, target.port)
        existing = self._transports.get(key)
        if existing is not None:
            return existing
        async with self._transport_lock:
            existing = self._transports.get(key)
            if existing is not None:
                return existing
            if len(self._transports) >= self._max_hosts:
                raise OAuthNetworkSecurityError("outbound host budget exhausted")
            transport = self._transport_factory()
            self._transports[key] = transport
            return transport

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """Resolve once, connect to that exact IP, and validate redirects/body bounds."""
        original_url = str(request.url)
        if _has_bearer_authorization(request.headers) and not self._policy.is_mcp_resource_request(
            original_url
        ):
            emit_client_security_audit(
                ClientSecurityAuditAction.OUTBOUND_BEARER_BLOCKED,
                ClientSecurityAuditOutcome.DENIED,
                reason="bearer_target_not_mcp_resource",
                target_kind="oauth_or_unexpected_origin",
            )
            raise OAuthNetworkSecurityError(
                "Bearer credentials may only be sent to the configured MCP resource endpoint"
            )
        target = await self._policy.resolve(original_url)
        child = await self._transport_for(target)

        headers = httpx2.Headers(request.headers)
        headers["Host"] = _host_header(target.host, target.port, target.scheme)
        extensions = dict(request.extensions)
        if target.scheme == "https":
            extensions["sni_hostname"] = target.host

        control_plane = self._policy.is_oauth_control_plane(original_url)
        if control_plane:
            headers["Accept-Encoding"] = "identity"
            timeout = self._policy.http_timeout_seconds
            extensions["timeout"] = {
                "connect": timeout,
                "read": timeout,
                "write": timeout,
                "pool": timeout,
            }

        pinned_url = request.url.copy_with(host=target.ip.compressed)
        network_request = httpx2.Request(
            request.method,
            pinned_url,
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = await child.handle_async_request(network_request)

        location = response.headers.get("Location")
        if 300 <= response.status_code < 400 and location:
            redirect_url = urljoin(original_url, location)
            try:
                redirect_target = await self._policy.resolve(redirect_url)
                if _origin(redirect_target.scheme, redirect_target.host, redirect_target.port) != (
                    _origin(target.scheme, target.host, target.port)
                ):
                    raise OAuthNetworkSecurityError(
                        "cross-origin redirects are not permitted for OAuth/MCP HTTP traffic"
                    )
            except Exception:
                await response.aclose()
                raise

        stream = response.stream
        if control_plane:
            content_encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
            if content_encoding not in {"", "identity"}:
                await response.aclose()
                raise OAuthNetworkSecurityError(
                    "compressed OAuth control-plane responses are not accepted"
                )
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    length = int(content_length)
                except ValueError:
                    await response.aclose()
                    raise OAuthNetworkSecurityError(
                        "OAuth response had an invalid Content-Length"
                    ) from None
                if length < 0 or length > self._policy.max_oauth_response_bytes:
                    await response.aclose()
                    raise OAuthNetworkSecurityError(
                        "OAuth response exceeded the configured size limit"
                    )
            stream = _BoundedAsyncByteStream(
                cast(httpx2.AsyncByteStream, response.stream),
                limit=self._policy.max_oauth_response_bytes,
            )

        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=stream,
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        """Close every per-host connection pool created by this transport."""
        transports = list(self._transports.values())
        self._transports.clear()
        for transport in transports:
            await transport.aclose()
