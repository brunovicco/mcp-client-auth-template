"""OAuth Client Credentials extension adapter for non-interactive MCP clients."""

from typing import Literal

from mcp.client.auth import TokenStorage
from mcp.client.auth.extensions.client_credentials import (
    ClientCredentialsOAuthProvider,
    PrivateKeyJWTOAuthProvider,
    SignedJWTParameters,
)

from mcp_client_auth_template.adapters.private_key_source import SigningKey

OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID = "io.modelcontextprotocol/oauth-client-credentials"
# OAuth registry value, not a credential.
_TOKEN_ENDPOINT_AUTH_METHOD: Literal["client_secret_basic"] = "client_secret_basic"  # noqa: S105
# Client assertions are single-use and short-lived; the SDK default is five minutes.
ASSERTION_LIFETIME_SECONDS = 60


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


def build_private_key_jwt_oauth_provider(
    *,
    server_url: str,
    storage: TokenStorage,
    client_id: str,
    signing_key: SigningKey,
    issuer: str,
    scope: str,
) -> PrivateKeyJWTOAuthProvider:
    """Build the SDK ``private_key_jwt`` provider with SDK-signed client assertions.

    The SDK signs each assertion with ``iss`` = ``sub`` = ``client_id``, a fresh ``jti``, and
    ``aud`` = the authorization server's issuer identifier (RFC 7523bis). It mints one only
    after metadata for exactly ``issuer`` has been discovered (ADR-0024, ADR-0025). The
    signing parameters live only inside the SDK's assertion callback, never on an object this
    adapter logs or stores.
    """
    assertion_provider = SignedJWTParameters(
        issuer=client_id,
        subject=client_id,
        signing_key=signing_key.pem.get_secret_value(),
        signing_algorithm=signing_key.algorithm,
        lifetime_seconds=ASSERTION_LIFETIME_SECONDS,
    ).create_assertion_provider()
    return PrivateKeyJWTOAuthProvider(
        server_url=server_url,
        storage=storage,
        client_id=client_id,
        assertion_provider=assertion_provider,
        scope=scope,
        issuer=issuer,
    )
