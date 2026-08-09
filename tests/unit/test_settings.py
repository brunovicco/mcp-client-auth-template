"""Unit tests for :class:`Settings`."""

from pathlib import Path

import pytest

from mcp_client_auth_template.entrypoints.settings import Settings

_ENTRA_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_ENTRA_CLIENT_ID = "22222222-2222-2222-2222-222222222222"


def test_entra_mode_accepts_matching_fields() -> None:
    settings = Settings(
        auth_provider="entra",
        server_url="https://mcp.example.invalid",
        entra_tenant_id=_ENTRA_TENANT_ID,
        entra_client_id=_ENTRA_CLIENT_ID,
    )

    assert settings.auth_provider == "entra"


def test_entra_mode_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="entra_tenant_id"):
        Settings(auth_provider="entra", server_url="https://mcp.example.invalid")


def test_entra_identifiers_must_be_uuids() -> None:
    with pytest.raises(ValueError, match="entra_client_id must be an application UUID"):
        Settings(
            auth_provider="entra",
            server_url="https://mcp.example.invalid",
            entra_tenant_id=_ENTRA_TENANT_ID,
            entra_client_id="client-abc",
        )


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


def test_ipv6_redirect_uri_brackets_the_loopback_literal() -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="https://mcp.example.invalid",
        redirect_host="::1",
        redirect_port=9999,
    )

    assert settings.redirect_uri == "http://[::1]:9999/callback"


@pytest.mark.parametrize(
    "host",
    ["localhost", "0.0.0.0", "10.0.0.10"],  # noqa: S104 - intentionally invalid bind targets
)
def test_redirect_host_must_be_loopback_ip_literal(host: str) -> None:
    with pytest.raises(ValueError, match="IP-literal loopback"):
        Settings(
            auth_provider="generic",
            server_url="https://mcp.example.invalid",
            redirect_host=host,
        )


@pytest.mark.parametrize("path", ["callback", "/callback?code=x", "/callback#fragment"])
def test_redirect_path_must_be_plain_absolute_path(path: str) -> None:
    with pytest.raises(ValueError, match="redirect_path"):
        Settings(
            auth_provider="generic",
            server_url="https://mcp.example.invalid",
            redirect_path=path,
        )


def test_http_server_requires_explicit_loopback_escape() -> None:
    with pytest.raises(ValueError, match="oauth_allow_insecure_loopback=true"):
        Settings(
            auth_provider="generic",
            server_url="http://127.0.0.1:8000",
            oauth_allow_insecure_loopback=False,
        )


def test_http_server_is_allowed_for_explicit_local_development() -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="http://127.0.0.1:8000",
        oauth_allow_insecure_loopback=True,
    )

    assert settings.server_url == "http://127.0.0.1:8000"


def test_http_server_rejects_non_loopback_even_with_development_escape() -> None:
    with pytest.raises(ValueError, match="only for loopback hosts"):
        Settings(
            auth_provider="generic",
            server_url="http://192.0.2.10:8000",
            oauth_allow_insecure_loopback=True,
        )


@pytest.mark.parametrize(
    "server_url",
    [
        "https://user:password@mcp.example.invalid",
        "https://mcp.example.invalid?token=secret",
        "https://mcp.example.invalid#fragment",
    ],
)
def test_server_url_rejects_credentials_query_and_fragment(server_url: str) -> None:
    with pytest.raises(ValueError, match="credentials, query, or fragment"):
        Settings(auth_provider="generic", server_url=server_url)


def test_generic_client_metadata_url_requires_https() -> None:
    with pytest.raises(ValueError, match="absolute HTTPS URL"):
        Settings(
            auth_provider="generic",
            server_url="https://mcp.example.invalid",
            generic_client_metadata_url="http://metadata.example.invalid/client.json",
        )


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
        entra_tenant_id=_ENTRA_TENANT_ID,
        entra_client_id=_ENTRA_CLIENT_ID,
    )

    assert "entra_client_secret" not in type(settings).model_fields


@pytest.mark.parametrize(
    "field_name",
    [
        "oauth_callback_timeout_seconds",
        "oauth_callback_max_requests",
        "http_connect_timeout_seconds",
        "http_read_timeout_seconds",
        "http_write_timeout_seconds",
        "http_pool_timeout_seconds",
        "tool_call_timeout_seconds",
        "shutdown_timeout_seconds",
    ],
)
def test_operational_budgets_must_be_positive(field_name: str) -> None:
    values: dict[str, object] = {
        "auth_provider": "generic",
        "server_url": "https://mcp.example.invalid",
        field_name: 0,
    }

    with pytest.raises(ValueError, match=field_name):
        Settings.model_validate(values)
