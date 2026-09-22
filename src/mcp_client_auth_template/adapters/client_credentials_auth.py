"""OAuth Client Credentials extension adapter for non-interactive MCP clients."""

from typing import Literal

from mcp.client.auth import TokenStorage
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider

OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID = "io.modelcontextprotocol/oauth-client-credentials"
# OAuth registry value, not a credential.
_TOKEN_ENDPOINT_AUTH_METHOD: Literal["client_secret_basic"] = "client_secret_basic"  # noqa: S105


def build_client_credentials_oauth_provider(
    *,
    server_url: str,
    storage: TokenStorage,
    client_id: str,
    client_secret: str,
    issuer: str,
    scope: str,
) -> ClientCredentialsOAuthProvider:
    """Build the SDK provider with pre-registered credentials and HTTP Basic auth.

    ``issuer`` binds the credential to one authorization server: the SDK builds a token
    request only from metadata discovered for exactly that issuer, and stops before sending
    anything if the MCP server's Protected Resource Metadata leads elsewhere (ADR-0024).
    """
    return ClientCredentialsOAuthProvider(
        server_url=server_url,
        storage=storage,
        client_id=client_id,
        client_secret=client_secret,
        token_endpoint_auth_method=_TOKEN_ENDPOINT_AUTH_METHOD,
        scope=scope,
        issuer=issuer,
    )
