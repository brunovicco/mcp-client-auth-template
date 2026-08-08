"""Unit tests for :func:`build_entra_oauth_provider`."""

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull
from pydantic import AnyUrl

from mcp_client_auth_template.adapters.entra_client_auth import build_entra_oauth_provider
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_EXPECTED_ISSUER = f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0"
_REDIRECT_URI = "http://127.0.0.1:8765/callback"


async def _noop_redirect(_url: str) -> None:
    return None


async def _unused_callback() -> AuthorizationCodeResult:  # pragma: no cover - never invoked here
    raise AssertionError("callback_handler should not run in these tests")


async def _build(
    storage: InMemoryTokenStorage, *, client_secret: str | None = None
) -> OAuthClientProvider:
    return await build_entra_oauth_provider(
        server_url="https://mcp.example.invalid",
        tenant_id=_TENANT_ID,
        client_id="client-abc",
        client_secret=client_secret,
        redirect_uri=_REDIRECT_URI,
        storage=storage,
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )


async def test_returns_an_oauth_client_provider() -> None:
    provider = await _build(InMemoryTokenStorage())

    assert isinstance(provider, OAuthClientProvider)


async def test_pre_seeds_client_id_and_tenant_issuer_so_cimd_and_dcr_are_skipped() -> None:
    storage = InMemoryTokenStorage()

    await _build(storage)

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "client-abc"
    assert client_info.issuer == _EXPECTED_ISSUER


async def test_upgrades_a_legacy_unbound_entra_client_record() -> None:
    storage = InMemoryTokenStorage()
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-abc",
            redirect_uris=[AnyUrl(_REDIRECT_URI)],
        )
    )

    await _build(storage)

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "client-abc"
    assert client_info.issuer == _EXPECTED_ISSUER


async def test_replaces_client_info_bound_to_a_different_authorization_server() -> None:
    storage = InMemoryTokenStorage()
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-abc",
            redirect_uris=[AnyUrl(_REDIRECT_URI)],
            issuer="https://login.microsoftonline.com/attacker-tenant/v2.0",
        )
    )

    await _build(storage)

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "client-abc"
    assert client_info.issuer == _EXPECTED_ISSUER


async def test_keeps_a_client_record_already_bound_to_the_expected_issuer() -> None:
    storage = InMemoryTokenStorage()
    existing = OAuthClientInformationFull(
        client_id="client-abc",
        redirect_uris=[AnyUrl(_REDIRECT_URI)],
        issuer=_EXPECTED_ISSUER,
        client_name="existing-bound-record",
    )
    await storage.set_client_info(existing)

    await _build(storage)

    assert await storage.get_client_info() is existing


async def test_a_confidential_client_gets_client_secret_post_and_issuer_binding() -> None:
    storage = InMemoryTokenStorage()

    await _build(storage, client_secret="super-secret-value")

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.token_endpoint_auth_method == "client_secret_post"
    assert client_info.issuer == _EXPECTED_ISSUER
