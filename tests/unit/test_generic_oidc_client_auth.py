"""Unit tests for :func:`build_generic_oauth_provider`."""

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.generic_oidc_client_auth import build_generic_oauth_provider
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage


async def _noop_redirect(_url: str) -> None:
    return None


async def _unused_callback() -> AuthorizationCodeResult:  # pragma: no cover - never invoked here
    raise AssertionError("callback_handler should not run in these tests")


async def test_returns_an_oauth_client_provider() -> None:
    provider = await build_generic_oauth_provider(
        server_url="https://mcp.example.invalid",
        redirect_uri="http://127.0.0.1:8765/callback",
        storage=InMemoryTokenStorage(),
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    assert isinstance(provider, OAuthClientProvider)


async def test_does_not_pre_seed_client_info_so_cimd_or_dcr_can_run() -> None:
    storage = InMemoryTokenStorage()

    await build_generic_oauth_provider(
        server_url="https://mcp.example.invalid",
        redirect_uri="http://127.0.0.1:8765/callback",
        storage=storage,
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    assert await storage.get_client_info() is None
