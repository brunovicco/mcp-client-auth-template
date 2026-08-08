"""End-to-end demo: authenticate and call the companion server's example tools.

Run against the server template (``mcp-server-auth-template``) with:

    uv run python -m mcp_client_auth_template.entrypoints.demo_client

The first run opens a browser for the authorization code flow; if
``MCP_CLIENT_TOKEN_STORAGE_PATH`` resolves to a real path (the default,
``~/.mcp-client-auth-template/tokens.json``), later runs reuse the stored
token and refresh it silently instead of prompting again.
"""

from collections.abc import Awaitable, Callable

import anyio
import httpx2
import structlog
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.browser_redirect import open_system_browser
from mcp_client_auth_template.adapters.entra_client_auth import build_entra_oauth_provider
from mcp_client_auth_template.adapters.generic_oidc_client_auth import build_generic_oauth_provider
from mcp_client_auth_template.adapters.loopback_callback_server import LoopbackCallbackServer
from mcp_client_auth_template.adapters.token_storage import FileTokenStorage, InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.logging import configure_logging
from mcp_client_auth_template.entrypoints.settings import Settings

logger = structlog.get_logger(__name__)

# Mirrors mcp.shared._httpx_utils.create_mcp_http_client's defaults (not used directly
# since that module isn't part of the SDK's exported public surface): a longer read
# timeout than connect/write/pool, since the streamable-HTTP transport holds the
# response stream open for server-sent events.
_HTTP_TIMEOUT = httpx2.Timeout(30.0, read=300.0)


def build_token_storage(settings: Settings) -> TokenStorage:
    """Return a persistent :class:`FileTokenStorage` or, with no path configured, in-memory."""
    if settings.token_storage_path is None:
        return InMemoryTokenStorage()
    return FileTokenStorage(settings.token_storage_path)


async def build_oauth_provider(
    settings: Settings,
    *,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]],
) -> OAuthClientProvider:
    """Return the adapter matching ``settings.auth_provider``."""
    if settings.auth_provider == "entra":
        if not (settings.entra_tenant_id and settings.entra_client_id):
            raise RuntimeError("auth_provider=entra requires entra_tenant_id and entra_client_id")
        return await build_entra_oauth_provider(
            server_url=settings.server_url,
            tenant_id=settings.entra_tenant_id,
            client_id=settings.entra_client_id,
            client_secret=settings.entra_client_secret,
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


async def run_demo() -> None:
    """Authenticate against the configured provider and call ``whoami`` and ``health``."""
    settings = Settings()  # values come from the environment
    configure_logging(service="mcp-client-auth-template", environment="local", version="0.1.0")
    storage = build_token_storage(settings)
    loopback = LoopbackCallbackServer(
        host=settings.redirect_host, port=settings.redirect_port, path=settings.redirect_path
    )

    oauth_provider = await build_oauth_provider(
        settings,
        storage=storage,
        redirect_handler=open_system_browser,
        callback_handler=loopback.wait_for_callback,
    )

    async with (
        httpx2.AsyncClient(
            auth=oauth_provider, follow_redirects=True, timeout=_HTTP_TIMEOUT
        ) as http_client,
        streamable_http_client(f"{settings.server_url}/mcp", http_client=http_client) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()

        whoami = await session.call_tool("whoami")
        logger.info("whoami", result=whoami.structured_content)

        health = await session.call_tool("health")
        logger.info("health", result=health.structured_content)


def main() -> None:
    """Synchronous entrypoint for ``uv run python -m ...``."""
    anyio.run(run_demo)


if __name__ == "__main__":
    main()
