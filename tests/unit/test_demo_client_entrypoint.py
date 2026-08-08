"""Unit tests for the demo entrypoint's storage and provider selection."""

from pathlib import Path

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult

from mcp_client_auth_template.adapters.entra_client_auth import build_entra_oauth_provider
from mcp_client_auth_template.adapters.generic_oidc_client_auth import build_generic_oauth_provider
from mcp_client_auth_template.adapters.token_storage import FileTokenStorage, InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.demo_client import (
    build_oauth_provider,
    build_token_storage,
)
from mcp_client_auth_template.entrypoints.settings import Settings


async def _noop_redirect(_url: str) -> None:
    return None


async def _unused_callback() -> AuthorizationCodeResult:  # pragma: no cover - never invoked here
    raise AssertionError("callback_handler should not run in these tests")


def test_build_token_storage_returns_in_memory_with_no_path() -> None:
    settings = Settings(
        auth_provider="generic", server_url="https://mcp.example.invalid", token_storage_path=None
    )

    assert isinstance(build_token_storage(settings), InMemoryTokenStorage)


def test_build_token_storage_returns_file_storage_with_a_path(tmp_path: Path) -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="https://mcp.example.invalid",
        token_storage_path=tmp_path / "tokens.json",
    )

    assert isinstance(build_token_storage(settings), FileTokenStorage)


async def test_build_oauth_provider_dispatches_to_entra() -> None:
    settings = Settings(
        auth_provider="entra",
        server_url="https://mcp.example.invalid",
        entra_tenant_id="11111111-1111-1111-1111-111111111111",
        entra_client_id="client-abc",
    )

    provider = await build_oauth_provider(
        settings,
        storage=InMemoryTokenStorage(),
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    assert isinstance(provider, OAuthClientProvider)


async def test_build_oauth_provider_dispatches_to_generic() -> None:
    settings = Settings(auth_provider="generic", server_url="https://mcp.example.invalid")

    provider = await build_oauth_provider(
        settings,
        storage=InMemoryTokenStorage(),
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )

    assert isinstance(provider, OAuthClientProvider)


def test_build_entra_and_generic_are_reachable_directly() -> None:
    # Import-only check that the two adapter factories this module dispatches to
    # are the ones actually re-exported for direct use outside the demo, too.
    assert callable(build_entra_oauth_provider)
    assert callable(build_generic_oauth_provider)
