"""Builds an ``OAuthClientProvider`` pre-bound to a pre-registered Entra ID app.

Entra ID supports neither Dynamic Client Registration nor Client ID Metadata
Documents (see the server template's ``docs/adr/0002-oauth21-resource-server.md``),
so a real Entra integration always starts from a client already registered out
of band in the Entra portal (or via Graph/CLI). This module's only job is
seeding that ``client_id`` into storage *before* ``OAuthClientProvider`` ever
runs its "do I have client info?" check, so it skips CIMD/DCR entirely
instead of failing against endpoints Entra does not expose.
"""

from collections.abc import Awaitable, Callable

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata
from pydantic import AnyUrl

_DEFAULT_SCOPE = "openid profile"


async def build_entra_oauth_provider(
    *,
    server_url: str,
    tenant_id: str,
    client_id: str,
    redirect_uri: str,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]],
    client_secret: str | None = None,
    scope: str = _DEFAULT_SCOPE,
) -> OAuthClientProvider:
    """Return a provider that authenticates against one Entra tenant's app registration.

    ``tenant_id`` is not passed to ``OAuthClientProvider`` directly - it only
    shapes the discovery URLs the flow resolves from the resource server's
    Protected Resource Metadata, which must point back at
    ``https://login.microsoftonline.com/{tenant_id}/v2.0`` (exactly what
    the companion server template's ``EntraTokenVerifier`` is configured
    with) for this to land on the right tenant.
    """
    auth_method = "client_secret_post" if client_secret else None
    redirect_url = AnyUrl(redirect_uri)
    client_metadata = OAuthClientMetadata(
        redirect_uris=[redirect_url],
        scope=scope,
        token_endpoint_auth_method=auth_method,
        client_name="mcp-client-auth-template (Entra ID)",
    )

    existing = await storage.get_client_info()
    if existing is None or existing.client_id != client_id:
        await storage.set_client_info(
            OAuthClientInformationFull(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=[redirect_url],
                token_endpoint_auth_method=auth_method,
            )
        )

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
