"""Build a public native Entra OAuth provider with exact authorization-server pinning.

The interactive CLI is an RFC 8252 public client: it uses authorization code +
PKCE S256 and never accepts or persists a client secret. Entra does not support
CIMD/DCR for this scenario, so the client ID is pre-registered out of band and
pre-seeded into SDK storage with ``token_endpoint_auth_method="none"``.

The SDK's normal issuer binding intentionally discards credentials and may then
fall back to registration if Protected Resource Metadata points at a different
authorization server. That behavior is correct for generic clients but not for a
pre-registered Entra client. ``PinnedEntraOAuthClientProvider`` therefore adds a
small fail-closed guard around the SDK's public ``async_auth_flow`` interface: no
OAuth request is allowed to leave the process unless it targets the configured
resource server's PRM endpoint or the exact tenant-scoped Entra authorization
server endpoints.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from urllib.parse import SplitResult, urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider, OAuthFlowError, TokenStorage
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata
from pydantic import AnyUrl

_DEFAULT_SCOPE = "openid profile"
_ENTRA_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/v2.0"
_PUBLIC_CLIENT_AUTH_METHOD = "none"


def _issuer_for_tenant(tenant_id: str) -> str:
    """Return the exact Entra v2 issuer for one tenant-specific authority."""
    if (
        not tenant_id
        or tenant_id != tenant_id.strip()
        or any(character in tenant_id for character in "/\\?#")
        or tenant_id.lower() in {"common", "organizations", "consumers"}
    ):
        raise ValueError("tenant_id must identify one concrete Entra tenant")
    return _ENTRA_ISSUER_TEMPLATE.format(tenant_id=tenant_id)


def _origin(url: str) -> tuple[str, str, int]:
    """Return a normalized origin tuple suitable for strict same-origin checks."""
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise ValueError(f"URL has no hostname: {url!r}")
    if parsed.scheme == "https":
        default_port = 443
    elif parsed.scheme == "http":
        default_port = 80
    else:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or default_port


def _entra_paths(tenant_id: str) -> tuple[frozenset[str], str, str]:
    """Return allowed metadata paths plus token and authorization endpoints for one tenant."""
    issuer_path = f"/{tenant_id}/v2.0"
    metadata_paths = frozenset(
        {
            f"/.well-known/oauth-authorization-server{issuer_path}",
            f"/.well-known/openid-configuration{issuer_path}",
            f"{issuer_path}/.well-known/openid-configuration",
        }
    )
    token_path = f"/{tenant_id}/oauth2/v2.0/token"
    authorization_path = f"/{tenant_id}/oauth2/v2.0/authorize"
    return metadata_paths, token_path, authorization_path


def _public_client_record_matches(
    client_info: OAuthClientInformationFull,
    *,
    client_id: str,
    issuer: str,
    redirect_uri: AnyUrl,
) -> bool:
    """Return whether stored client metadata is the canonical secret-free Entra record."""
    redirect_uris = client_info.redirect_uris or []
    return (
        client_info.client_id == client_id
        and client_info.issuer == issuer
        and client_info.client_secret is None
        and client_info.token_endpoint_auth_method == _PUBLIC_CLIENT_AUTH_METHOD
        and [str(item) for item in redirect_uris] == [str(redirect_uri)]
    )


class PinnedEntraOAuthClientProvider(OAuthClientProvider):
    """OAuth provider that permits only the configured Entra tenant as authorization server."""

    def __init__(
        self,
        *,
        expected_issuer: str,
        tenant_id: str,
        server_url: str,
        client_metadata: OAuthClientMetadata,
        storage: TokenStorage,
        redirect_handler: Callable[[str], Awaitable[None]],
        callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]],
    ) -> None:
        """Initialize an Entra provider pinned to one tenant and resource origin."""
        super().__init__(
            server_url=server_url,
            client_metadata=client_metadata,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        self._expected_issuer = expected_issuer
        self._expected_as_origin = _origin(expected_issuer)
        self._resource_origin = _origin(server_url)
        self._metadata_paths, self._token_path, self._authorization_path = _entra_paths(tenant_id)

    def _assert_allowed_request(
        self, outbound: httpx2.Request, *, initial_request: httpx2.Request
    ) -> None:
        """Reject any SDK-generated OAuth request outside the pinned trust boundary."""
        outbound_url = str(outbound.url)
        parsed = urlsplit(outbound_url)

        if outbound is initial_request:
            if _origin(outbound_url) != self._resource_origin:
                raise OAuthFlowError("MCP resource request escaped the configured resource origin")
            return

        if _origin(outbound_url) == self._resource_origin:
            if outbound.method == "GET" and parsed.path.startswith(
                "/.well-known/oauth-protected-resource"
            ):
                return
            raise OAuthFlowError(
                "Entra public-client flow refused a non-PRM request to the resource server"
            )

        if _origin(outbound_url) != self._expected_as_origin:
            raise OAuthFlowError(
                "Entra authorization-server pin mismatch: "
                f"expected {self._expected_issuer!r}, got {outbound_url!r}"
            )

        if outbound.method == "GET" and parsed.path in self._metadata_paths:
            return
        if outbound.method == "POST" and parsed.path == self._token_path:
            return

        raise OAuthFlowError(
            "Entra public-client flow refused an unexpected authorization-server endpoint: "
            f"{outbound.method} {parsed.path}"
        )

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """Delegate to the SDK while checking each yielded network request before I/O."""
        flow = super().async_auth_flow(request)
        try:
            outbound = await anext(flow)
            while True:
                self._assert_allowed_request(outbound, initial_request=request)
                response = yield outbound
                outbound = await flow.asend(response)
        except StopAsyncIteration:
            return
        finally:
            await flow.aclose()

    def assert_authorization_url(self, url: str) -> None:
        """Reject browser redirects that do not target this exact tenant's authorize endpoint."""
        parsed: SplitResult = urlsplit(url)
        if _origin(url) != self._expected_as_origin or parsed.path != self._authorization_path:
            raise OAuthFlowError(
                "Entra authorization URL pin mismatch: "
                f"expected tenant endpoint {self._authorization_path!r}, got {url!r}"
            )


async def build_entra_oauth_provider(
    *,
    server_url: str,
    tenant_id: str,
    client_id: str,
    redirect_uri: str,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]],
    scope: str = _DEFAULT_SCOPE,
) -> OAuthClientProvider:
    """Return a secret-free public client pinned to one Entra tenant.

    A stored confidential-client record from an older template version is
    replaced before the SDK loads it, ensuring a legacy ``client_secret`` cannot
    silently survive the migration to the native public-client contract.
    """
    redirect_url = AnyUrl(redirect_uri)
    issuer = _issuer_for_tenant(tenant_id)
    client_metadata = OAuthClientMetadata(
        redirect_uris=[redirect_url],
        scope=scope,
        token_endpoint_auth_method=_PUBLIC_CLIENT_AUTH_METHOD,
        client_name="mcp-client-auth-template (Entra ID public client)",
    )

    existing = await storage.get_client_info()
    if existing is None or not _public_client_record_matches(
        existing, client_id=client_id, issuer=issuer, redirect_uri=redirect_url
    ):
        await storage.set_client_info(
            OAuthClientInformationFull(
                client_id=client_id,
                client_secret=None,
                redirect_uris=[redirect_url],
                token_endpoint_auth_method=_PUBLIC_CLIENT_AUTH_METHOD,
                issuer=issuer,
            )
        )

    provider: PinnedEntraOAuthClientProvider

    async def pinned_redirect(url: str) -> None:
        provider.assert_authorization_url(url)
        await redirect_handler(url)

    provider = PinnedEntraOAuthClientProvider(
        expected_issuer=issuer,
        tenant_id=tenant_id,
        server_url=server_url,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=pinned_redirect,
        callback_handler=callback_handler,
    )
    return provider
