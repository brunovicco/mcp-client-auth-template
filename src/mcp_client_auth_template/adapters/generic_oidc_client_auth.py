"""Builds an ``OAuthClientProvider`` for any standards-compliant OIDC authorization server.

Unlike the Entra adapter, this one never pre-seeds client info: the SDK's own
``should_use_client_metadata_url`` picks Client ID Metadata Documents (CIMD)
whenever ``client_metadata_url`` is set *and* the authorization server
advertises ``client_id_metadata_document_supported``, and falls back to
Dynamic Client Registration otherwise - exactly the CIMD-first-with-DCR-
fallback behavior the MCP 2026-07-28 spec expects, with no branching needed
here.

``client_metadata_url``, when set, must point at a stable HTTPS URL that
serves this same client's ``OAuthClientMetadata`` as JSON (a static file
behind any web host works). Leaving it unset - the right choice for a local
demo, since ``is_valid_client_metadata_url`` requires HTTPS and a loopback
redirect has none - makes the flow fall back to DCR automatically.
"""

from collections.abc import Awaitable, Callable

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from pydantic import AnyUrl

_DEFAULT_SCOPE = "openid profile"


async def build_generic_oauth_provider(
    *,
    server_url: str,
    redirect_uri: str,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]],
    client_metadata_url: str | None = None,
    scope: str = _DEFAULT_SCOPE,
) -> OAuthClientProvider:
    """Return a provider that registers itself (via CIMD or DCR) against ``server_url``'s AS."""
    client_metadata = OAuthClientMetadata(
        redirect_uris=[AnyUrl(redirect_uri)],
        scope=scope,
        client_name="mcp-client-auth-template",
    )

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        client_metadata_url=client_metadata_url,
    )
