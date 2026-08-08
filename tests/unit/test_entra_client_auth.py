"""Unit tests for the hardened Entra public-client adapter."""

import httpx2
import pytest
from mcp.client.auth import OAuthClientProvider, OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull
from pydantic import AnyUrl

from mcp_client_auth_template.adapters.entra_client_auth import (
    PinnedEntraOAuthClientProvider,
    build_entra_oauth_provider,
)
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_EXPECTED_ISSUER = f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0"
_REDIRECT_URI = "http://127.0.0.1:8765/callback"
_SERVER_URL = "https://mcp.example.invalid"


async def _noop_redirect(_url: str) -> None:
    return None


async def _unused_callback() -> AuthorizationCodeResult:  # pragma: no cover - never invoked here
    raise AssertionError("callback_handler should not run in these tests")


async def _build(storage: InMemoryTokenStorage) -> OAuthClientProvider:
    return await build_entra_oauth_provider(
        server_url=_SERVER_URL,
        tenant_id=_TENANT_ID,
        client_id="client-abc",
        redirect_uri=_REDIRECT_URI,
        storage=storage,
        redirect_handler=_noop_redirect,
        callback_handler=_unused_callback,
    )


async def test_returns_a_pinned_oauth_client_provider() -> None:
    provider = await _build(InMemoryTokenStorage())

    assert isinstance(provider, PinnedEntraOAuthClientProvider)
    assert isinstance(provider, OAuthClientProvider)


async def test_pre_seeds_secret_free_public_client_bound_to_tenant_issuer() -> None:
    storage = InMemoryTokenStorage()

    await _build(storage)

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "client-abc"
    assert client_info.client_secret is None
    assert client_info.token_endpoint_auth_method == "none"
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
    assert client_info.token_endpoint_auth_method == "none"
    assert client_info.issuer == _EXPECTED_ISSUER


async def test_removes_a_legacy_confidential_client_secret_from_storage() -> None:
    storage = InMemoryTokenStorage()
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-abc",
            client_secret="legacy-secret",
            redirect_uris=[AnyUrl(_REDIRECT_URI)],
            token_endpoint_auth_method="client_secret_post",
            issuer=_EXPECTED_ISSUER,
        )
    )

    await _build(storage)

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_secret is None
    assert client_info.token_endpoint_auth_method == "none"


async def test_replaces_client_info_bound_to_a_different_authorization_server() -> None:
    storage = InMemoryTokenStorage()
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="client-abc",
            redirect_uris=[AnyUrl(_REDIRECT_URI)],
            token_endpoint_auth_method="none",
            issuer="https://login.microsoftonline.com/attacker-tenant/v2.0",
        )
    )

    await _build(storage)

    client_info = await storage.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "client-abc"
    assert client_info.issuer == _EXPECTED_ISSUER


async def test_keeps_a_canonical_public_client_record() -> None:
    storage = InMemoryTokenStorage()
    existing = OAuthClientInformationFull(
        client_id="client-abc",
        redirect_uris=[AnyUrl(_REDIRECT_URI)],
        token_endpoint_auth_method="none",
        issuer=_EXPECTED_ISSUER,
    )
    await storage.set_client_info(existing)

    await _build(storage)

    assert await storage.get_client_info() is existing


async def test_multi_tenant_entra_authorities_are_rejected() -> None:
    with pytest.raises(ValueError, match="one concrete Entra tenant"):
        await build_entra_oauth_provider(
            server_url=_SERVER_URL,
            tenant_id="common",
            client_id="client-abc",
            redirect_uri=_REDIRECT_URI,
            storage=InMemoryTokenStorage(),
            redirect_handler=_noop_redirect,
            callback_handler=_unused_callback,
        )


async def test_browser_authorization_url_must_target_exact_tenant_endpoint() -> None:
    provider = await _build(InMemoryTokenStorage())
    assert isinstance(provider, PinnedEntraOAuthClientProvider)

    provider.assert_authorization_url(
        f"https://login.microsoftonline.com/{_TENANT_ID}/oauth2/v2.0/authorize?client_id=x"
    )

    with pytest.raises(OAuthFlowError, match="authorization URL pin mismatch"):
        provider.assert_authorization_url(
            "https://login.microsoftonline.com/attacker/oauth2/v2.0/authorize?client_id=x"
        )


async def test_prm_cannot_redirect_oauth_discovery_to_an_unexpected_authorization_server() -> None:
    provider = await _build(InMemoryTokenStorage())
    request = httpx2.Request("POST", f"{_SERVER_URL}/mcp")
    flow = provider.async_auth_flow(request)

    outbound = await anext(flow)
    assert outbound is request

    challenge = httpx2.Response(
        401,
        headers={
            "WWW-Authenticate": (
                'Bearer resource_metadata="https://mcp.example.invalid/'
                '.well-known/oauth-protected-resource"'
            )
        },
        request=outbound,
    )
    prm_request = await flow.asend(challenge)
    assert str(prm_request.url) == (
        "https://mcp.example.invalid/.well-known/oauth-protected-resource"
    )

    prm_response = httpx2.Response(
        200,
        json={
            "resource": _SERVER_URL,
            "authorization_servers": ["https://attacker.example.invalid"],
        },
        request=prm_request,
    )
    with pytest.raises(OAuthFlowError, match="authorization-server pin mismatch"):
        await flow.asend(prm_response)
