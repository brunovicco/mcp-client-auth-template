"""End-to-end demo: authenticate and call the companion server's example tools.

Run against the server template (``mcp-server-auth-template``) with:

    uv run python -m mcp_client_auth_template.entrypoints.demo_client

The first run opens a browser for the authorization code flow; if
``MCP_CLIENT_TOKEN_STORAGE_PATH`` resolves to a real path (the default,
``~/.mcp-client-auth-template/tokens.json``), later runs reuse the stored
token and refresh it silently instead of prompting again.
"""

from collections.abc import Awaitable, Callable
from typing import cast

import anyio
import httpx
import httpx2
import structlog
from a2a_otel_kit.adapters.mcp import TracingAsyncTransport
from a2a_otel_kit.application.settings import ObservabilitySettings
from a2a_otel_kit.entrypoints.observability import Observability
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.browser_redirect import open_system_browser
from mcp_client_auth_template.adapters.entra_client_auth import build_entra_oauth_provider
from mcp_client_auth_template.adapters.generic_oidc_client_auth import build_generic_oauth_provider
from mcp_client_auth_template.adapters.loopback_callback_server import LoopbackCallbackServer
from mcp_client_auth_template.adapters.oauth_discovery_security import (
    OAuthDiscoverySecurityPolicy,
    PinnedDnsAsyncTransport,
)
from mcp_client_auth_template.adapters.token_storage import FileTokenStorage, InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.settings import Settings

logger = structlog.get_logger(__name__)

# Mirrors mcp.shared._httpx_utils.create_mcp_http_client's defaults (not used directly
# since that module isn't part of the SDK's exported public surface): a longer read
# timeout than connect/write/pool, since the streamable-HTTP transport may hold a
# response stream open for server-sent events.
_HTTP_TIMEOUT = httpx2.Timeout(30.0, read=300.0)

_SERVICE_NAME = "mcp-client-auth-template"
_SERVICE_VERSION = "0.1.0"
_ENVIRONMENT = "local"


def build_token_storage(settings: Settings) -> TokenStorage:
    """Return a persistent :class:`FileTokenStorage` or, with no path configured, in-memory."""
    if settings.token_storage_path is None:
        return InMemoryTokenStorage()
    return FileTokenStorage(settings.token_storage_path)


def build_oauth_network_policy(settings: Settings) -> OAuthDiscoverySecurityPolicy:
    """Build the fail-closed outbound policy shared by OAuth HTTP and browser redirects."""
    return OAuthDiscoverySecurityPolicy(
        resource_server_url=settings.server_url,
        allow_insecure_loopback=settings.oauth_allow_insecure_loopback,
        dns_timeout_seconds=settings.oauth_dns_timeout_seconds,
        http_timeout_seconds=settings.oauth_http_timeout_seconds,
        max_oauth_response_bytes=settings.oauth_max_response_bytes,
    )


def build_secure_http_transport(
    settings: Settings,
    *,
    policy: OAuthDiscoverySecurityPolicy,
    observability: Observability,
) -> httpx2.AsyncBaseTransport:
    """Wrap DNS-pinned egress with the existing a2a-otel-kit tracing transport."""
    secure_transport = PinnedDnsAsyncTransport(
        policy=policy,
        transport_factory=httpx2.AsyncHTTPTransport,
        max_hosts=settings.oauth_max_hosts,
    )
    traced = TracingAsyncTransport.wrap(
        cast(httpx.AsyncBaseTransport, secure_transport), observability
    )
    return cast(httpx2.AsyncBaseTransport, traced)


def build_observability_settings() -> ObservabilitySettings:
    """Build this demo's observability identity; export/logging config comes from the environment.

    Service identity is fixed for this single-entrypoint demo. ``ObservabilitySettings`` reads its
    remaining fields (``enabled``, ``otlp_endpoint``, ``otlp_timeout_seconds``, ``log_level``,
    ``log_format``) from ``A2A_OTEL_*``-prefixed environment variables itself; leaving them unset
    keeps ``enabled=False``, the network-silent default documented in ``docs/OBSERVABILITY.md``.
    """
    return ObservabilitySettings(
        service_name=_SERVICE_NAME, service_version=_SERVICE_VERSION, environment=_ENVIRONMENT
    )


async def build_oauth_provider(
    settings: Settings,
    *,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]],
) -> OAuthClientProvider:
    """Return the adapter matching ``settings.auth_provider``.

    ``Settings._finalize`` already enforces that ``entra_tenant_id``/``entra_client_id`` are set
    when ``auth_provider="entra"``; the ``cast`` calls below are type narrowing for mypy, not a
    second validation pass.
    """
    if settings.auth_provider == "entra":
        return await build_entra_oauth_provider(
            server_url=settings.server_url,
            tenant_id=cast(str, settings.entra_tenant_id),
            client_id=cast(str, settings.entra_client_id),
            redirect_uri=settings.redirect_uri,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            scope=settings.scope,
        )

    return await build_generic_oauth_provider(
        server_url=settings.server_url,
        redirect_uri=settings.redirect_uri,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        client_metadata_url=settings.generic_client_metadata_url,
        scope=settings.scope,
    )


def build_mcp_client(settings: Settings, *, http_client: httpx2.AsyncClient) -> Client:
    """Return the SDK v2 high-level client with automatic protocol negotiation.

    ``mode="auto"`` probes ``server/discover`` first. A 2026-07-28 server therefore
    remains on the modern sessionless protocol path, while older servers are still
    supported through the SDK-managed legacy initialization fallback.
    """
    transport = streamable_http_client(
        f"{settings.server_url.rstrip('/')}/mcp",
        http_client=http_client,
    )
    return Client(transport, mode="auto")


async def run_demo() -> None:
    """Authenticate against the configured provider and call ``whoami`` and ``health``."""
    settings = Settings()  # values come from the environment
    observability = Observability.configure(build_observability_settings())
    try:
        storage = build_token_storage(settings)
        network_policy = build_oauth_network_policy(settings)
        loopback = LoopbackCallbackServer(
            host=settings.redirect_host, port=settings.redirect_port, path=settings.redirect_path
        )

        oauth_provider = await build_oauth_provider(
            settings,
            storage=storage,
            redirect_handler=network_policy.wrap_browser_redirect(
                loopback.wrap_redirect_handler(open_system_browser)
            ),
            callback_handler=loopback.wait_for_callback,
        )

        # Tracing remains the outer transport so spans keep the logical hostname. The security
        # transport underneath resolves and pins the actual connect IP without exposing that
        # implementation detail as the application-level request URL.
        transport = build_secure_http_transport(
            settings, policy=network_policy, observability=observability
        )

        async with (
            httpx2.AsyncClient(
                auth=oauth_provider,
                follow_redirects=True,
                max_redirects=settings.oauth_max_redirects,
                timeout=_HTTP_TIMEOUT,
                transport=transport,
            ) as http_client,
            build_mcp_client(settings, http_client=http_client) as client,
        ):
            logger.info("mcp_connected", protocol_version=client.protocol_version)

            whoami = await client.call_tool("whoami")
            logger.info("whoami", result=whoami.structured_content)

            health = await client.call_tool("health")
            logger.info("health", result=health.structured_content)
    finally:
        observability.shutdown()


def main() -> None:
    """Synchronous entrypoint for ``uv run python -m ...``."""
    anyio.run(run_demo)


if __name__ == "__main__":
    main()
