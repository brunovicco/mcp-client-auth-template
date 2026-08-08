"""Unit tests for :class:`Settings`."""

from pathlib import Path

import pytest

from mcp_client_auth_template.entrypoints.settings import Settings


def test_entra_mode_accepts_matching_fields() -> None:
    settings = Settings(
        auth_provider="entra",
        server_url="https://mcp.example.invalid",
        entra_tenant_id="11111111-1111-1111-1111-111111111111",
        entra_client_id="client-abc",
    )

    assert settings.auth_provider == "entra"


def test_entra_mode_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="entra_tenant_id"):
        Settings(auth_provider="entra", server_url="https://mcp.example.invalid")


def test_generic_mode_needs_no_extra_fields() -> None:
    settings = Settings(auth_provider="generic", server_url="https://mcp.example.invalid")

    assert settings.generic_client_metadata_url is None


def test_redirect_uri_is_built_from_host_port_and_path() -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="https://mcp.example.invalid",
        redirect_host="127.0.0.1",
        redirect_port=9999,
        redirect_path="/oauth/callback",
    )

    assert settings.redirect_uri == "http://127.0.0.1:9999/oauth/callback"


def test_token_storage_path_expands_the_home_directory() -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="https://mcp.example.invalid",
        token_storage_path="~/.mcp-client-auth-template/tokens.json",
    )

    assert settings.token_storage_path is not None
    assert "~" not in str(settings.token_storage_path)
    assert str(settings.token_storage_path).startswith(str(Path.home()))


def test_interactive_entra_settings_expose_no_client_secret_field() -> None:
    settings = Settings(
        auth_provider="entra",
        server_url="https://mcp.example.invalid",
        entra_tenant_id="11111111-1111-1111-1111-111111111111",
        entra_client_id="client-abc",
    )

    assert "entra_client_secret" not in type(settings).model_fields
