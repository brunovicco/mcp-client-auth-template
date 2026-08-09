"""Tests for the client auth-provider and transport compatibility contract."""

import pytest
from pydantic import ValidationError
from scripts.auth_transport_contract import (
    Provider,
    TransportProfile,
    build_profile_settings,
    validate_profile,
)

from mcp_client_auth_template.entrypoints.preflight import validate_production_settings
from mcp_client_auth_template.entrypoints.settings import Settings

_PROVIDERS: tuple[Provider, ...] = ("entra", "generic")
_TRANSPORTS: tuple[TransportProfile, ...] = (
    "production-https",
    "loopback-ipv4",
    "loopback-ipv6",
)
_ENTRA: dict[str, object] = {
    "auth_provider": "entra",
    "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
    "entra_client_id": "22222222-2222-2222-2222-222222222222",
    "token_storage_path": None,
}


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_supported_matrix_cells_validate(
    provider: Provider,
    transport: TransportProfile,
) -> None:
    result = validate_profile(provider, transport)

    assert result["status"] == "ok"
    assert result["provider"] == provider
    assert result["transport"] == transport


def test_ipv6_loopback_redirect_is_bracketed() -> None:
    settings = build_profile_settings("generic", "loopback-ipv6")

    assert settings.redirect_uri == "http://[::1]:8765/callback"


def test_http_loopback_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match=r"oauth_allow_insecure_loopback=true"):
        Settings(
            server_url="http://127.0.0.1:8000/mcp",
            oauth_allow_insecure_loopback=False,
            auth_provider="entra",
            entra_tenant_id="11111111-1111-1111-1111-111111111111",
            entra_client_id="22222222-2222-2222-2222-222222222222",
            token_storage_path=None,
        )


def test_http_non_loopback_is_rejected_even_with_opt_in() -> None:
    with pytest.raises(ValidationError, match=r"allowed only for loopback hosts"):
        Settings.model_validate(
            {
                "server_url": "http://192.0.2.10:8000/mcp",
                "oauth_allow_insecure_loopback": True,
                **_ENTRA,
            }
        )


def test_redirect_listener_requires_ip_literal_loopback() -> None:
    with pytest.raises(ValidationError, match=r"IP-literal loopback"):
        Settings.model_validate(
            {
                "server_url": "https://mcp.acme.corp/mcp",
                "redirect_host": "localhost",
                **_ENTRA,
            }
        )


def test_production_rejects_insecure_loopback_escape() -> None:
    settings = Settings.model_validate(
        {
            "server_url": "https://mcp.acme.corp/mcp",
            "oauth_allow_insecure_loopback": True,
            **_ENTRA,
        }
    )

    issues = validate_production_settings(settings, "production")

    assert any(
        issue.location == "oauth_allow_insecure_loopback"
        and issue.type == "insecure_loopback_not_allowed"
        for issue in issues
    )


def test_production_rejects_http_even_when_local_opt_in_is_explicit() -> None:
    settings = Settings.model_validate(
        {
            "server_url": "http://127.0.0.1:8000/mcp",
            "oauth_allow_insecure_loopback": True,
            **_ENTRA,
        }
    )

    issues = validate_production_settings(settings, "production")

    assert any(
        issue.location == "server_url" and issue.type == "https_required_in_production"
        for issue in issues
    )


def test_generic_client_metadata_requires_https() -> None:
    with pytest.raises(ValidationError, match=r"generic_client_metadata_url must be"):
        Settings(
            auth_provider="generic",
            server_url="https://mcp.acme.corp/mcp",
            generic_client_metadata_url="http://127.0.0.1/client-metadata.json",
            token_storage_path=None,
        )
