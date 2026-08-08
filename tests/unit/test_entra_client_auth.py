"""Unit tests for :func:`build_entra_oauth_provider`."""

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.entra_client_auth import build_entra_oauth_provider
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage


async def _noop_redirect(_url: str) -> None:
    return None


async def _unused_callback() -> AuthorizationCodeResult:  # pragma: no cover - never invoked here
    raise AssertionError("callback_handler should not run in these tests")


async def test_returns_an_oauth_client_provider() -> None:
    provider = await build_entra_oauth_provider(
        server_url="https://mcp.example.invalid",
        tenant_id="11111111-1111-1111-1111-111111111111",
        client_id="client-abc",
        redirect_uri="http://127.0.0.1:8765/callback",
        storage=InMemoryTokenStorage(),
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    assert isinstance(provider, OAuthClientProvider)


async def test_pre_seeds_the_client_id_so_cimd_and_dcr_are_skipped() -> None:
    storage = InMemoryTokenStorage()

    await build_entra_oauth_provider(
        server_url="https://mcp.example.invalid",
        tenant_id="11111111-1111-1111-1111-111111111111",
        client_id="client-abc",
        redirect_uri="http://127.0.0.1:8765/callback",
        storage=storage,
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "client-abc"


async def test_a_confidential_client_gets_client_secret_post() -> None:
    storage = InMemoryTokenStorage()

    await build_entra_oauth_provider(
        server_url="https://mcp.example.invalid",
        tenant_id="11111111-1111-1111-1111-111111111111",
        client_id="client-abc",
        client_secret="super-secret-value",
        redirect_uri="http://127.0.0.1:8765/callback",
        storage=storage,
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.token_endpoint_auth_method == "client_secret_post"
