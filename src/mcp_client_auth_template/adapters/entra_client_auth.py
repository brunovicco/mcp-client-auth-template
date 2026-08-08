"""Builds an ``OAuthClientProvider`` pre-bound to a pre-registered Entra ID app.

Entra ID supports neither Dynamic Client Registration nor Client ID Metadata
Documents (see the server template's ``docs/adr/0002-oauth21-resource-server.md``),
so a real Entra integration always starts from a client already registered out
of band in the Entra portal (or via Graph/CLI). This module seeds that
``client_id`` into storage *before* ``OAuthClientProvider`` runs its "do I have
client info?" check, so it skips CIMD/DCR entirely.

The seeded record is also bound to the tenant-scoped Entra issuer. The MCP SDK
uses this SEP-2352 binding after Protected Resource Metadata discovery to reject
cross-authorization-server credential reuse before a token request is built.
"""

from collections.abc import Awaitable, Callable

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata
from pydantic import AnyUrl

_DEFAULT_SCOPE = "openid profile"
_ENTRA_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/v2.0"


def _issuer_for_tenant(tenant_id: str) -> str:
    """Return the exact Entra v2 issuer used to bind pre-registered credentials."""
    return _ENTRA_ISSUER_TEMPLATE.format(tenant_id=tenant_id)


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
    """Return a provider bound to one Entra tenant's pre-registered application.

    ``tenant_id`` serves two related purposes: the companion resource server's
    Protected Resource Metadata must resolve to the same tenant-scoped issuer,
    and the pre-seeded ``OAuthClientInformationFull`` is stamped with that issuer.
    If storage contains an older unbound record or one bound to a different
    issuer, it is replaced before the SDK loads it. Once discovery runs, the SDK
    can therefore fail closed if PRM advertises any other authorization server.
    """
    auth_method = "client_secret_post" if client_secret else None
    redirect_url = AnyUrl(redirect_uri)
    issuer = _issuer_for_tenant(tenant_id)
    client_metadata = OAuthClientMetadata(
        redirect_uris=[redirect_url],
        scope=scope,
        token_endpoint_auth_method=auth_method,
        client_name="mcp-client-auth-template (Entra ID)",
    )

    existing = await storage.get_client_info()
    if existing is None or existing.client_id != client_id or existing.issuer != issuer:
        await storage.set_client_info(
            OAuthClientInformationFull(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=[redirect_url],
                token_endpoint_auth_method=auth_method,
                issuer=issuer,
            )
        )

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
