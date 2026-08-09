"""Tests for the network-silent production configuration preflight."""

import json

import pytest

from mcp_client_auth_template.entrypoints import preflight
from mcp_client_auth_template.entrypoints.preflight import (
    ConfigurationPreflightError,
    load_validated_settings,
    validate_production_settings,
)
from mcp_client_auth_template.entrypoints.settings import Settings

_ENTRA_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_ENTRA_CLIENT_ID = "22222222-2222-2222-2222-222222222222"


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_provider": "entra",
        "server_url": "https://mcp.acme.com",
        "entra_tenant_id": _ENTRA_TENANT_ID,
        "entra_client_id": _ENTRA_CLIENT_ID,
        "oauth_allow_insecure_loopback": False,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_production_preflight_accepts_realistic_https_configuration() -> None:
    assert validate_production_settings(_production_settings(), "production") == []


def test_production_preflight_rejects_loopback_escape_and_placeholder_host() -> None:
    settings = Settings(
        auth_provider="generic",
        server_url="http://127.0.0.1:8000",
        oauth_allow_insecure_loopback=True,
    )

    issues = validate_production_settings(settings, "production")

    assert {issue.type for issue in issues} == {
        "https_required_in_production",
        "insecure_loopback_not_allowed",
    }


def test_production_preflight_rejects_placeholder_identifiers() -> None:
    settings = _production_settings(
        entra_tenant_id="00000000-0000-0000-0000-000000000000",
        entra_client_id="00000000-0000-0000-0000-000000000000",
    )

    issues = validate_production_settings(settings, "production")

    assert [issue.location for issue in issues] == ["entra_tenant_id", "entra_client_id"]


def test_cli_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_value = "https://user:super-secret@mcp.example.invalid"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MCP_CLIENT_SERVER_URL", secret_value)
    monkeypatch.setenv("MCP_CLIENT_AUTH_PROVIDER", "generic")
    monkeypatch.setattr("sys.argv", ["preflight", "--json"])

    with pytest.raises(SystemExit) as exc_info:
        preflight.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert payload["status"] == "error"
    assert payload["error"] == "configuration_invalid"
    assert "super-secret" not in output
    assert "user:" not in output


def test_load_validated_settings_fails_with_sanitized_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_value = "https://user:super-secret@mcp.example.invalid"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MCP_CLIENT_SERVER_URL", secret_value)
    monkeypatch.setenv("MCP_CLIENT_AUTH_PROVIDER", "generic")

    with pytest.raises(ConfigurationPreflightError) as exc_info:
        load_validated_settings()

    assert "super-secret" not in str(exc_info.value)
    assert "user:" not in str(exc_info.value)


def test_invalid_environment_is_reported_without_loading_network_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod-ish")

    settings, environment, issues = preflight.run_preflight()

    assert settings is None
    assert environment is None
    assert issues == [preflight.PreflightIssue("APP_ENV", "invalid_environment")]
