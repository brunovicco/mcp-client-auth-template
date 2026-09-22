"""The client exactly as the demo entrypoint composes it, for real-wire integration tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a2a_otel_kit.entrypoints.observability import Observability
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider

from mcp_client_auth_template.entrypoints.demo_client import (
    build_http_client,
    build_mcp_client,
    build_oauth_network_policy,
    build_observability_settings,
    build_secure_http_transport,
)
from mcp_client_auth_template.entrypoints.settings import Settings


@asynccontextmanager
async def production_client(
    settings: Settings, *, oauth_provider: OAuthClientProvider
) -> AsyncIterator[Client]:
    """Connect through the DNS-pinned, redirect-bounded production HTTP stack."""
    observability = Observability.configure(build_observability_settings())
    transport = build_secure_http_transport(
        settings, policy=build_oauth_network_policy(settings), observability=observability
    )
    try:
        async with (
            build_http_client(
                settings, oauth_provider=oauth_provider, transport=transport
            ) as http_client,
            build_mcp_client(settings, http_client=http_client) as client,
        ):
            yield client
    finally:
        observability.shutdown()
