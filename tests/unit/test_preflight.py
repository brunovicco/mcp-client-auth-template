"""Tests for the network-silent production configuration preflight."""

import json

import pytest

from mcp_client_auth_template.entrypoints import preflight
from mcp_client_auth_template.entrypoints.preflight import (
    ConfigurationPreflightError,
    load_validated_settings,
    validate_production_settings,
    validate_signing_key,
)
from mcp_client_auth_template.entrypoints.settings import Settings
from tests.key_material import pem, rsa_key, secure_key_dir, write_key

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


def _machine_settings(issuer: str, **overrides: object) -> Settings:
    return _production_settings(
        auth_provider="generic",
        auth_mode="client_credentials",
        client_credentials_client_id="machine-client",
        client_credentials_secret="unit-test-credential",
        client_credentials_issuer=issuer,
        **overrides,
    )


def test_production_preflight_accepts_a_real_https_machine_issuer() -> None:
    settings = _machine_settings("https://login.acme.com/oauth2")

    assert validate_production_settings(settings, "production") == []


def test_production_preflight_rejects_loopback_or_placeholder_machine_issuer() -> None:
    loopback = _machine_settings("http://127.0.0.1:9000", oauth_allow_insecure_loopback=True)
    placeholder = _machine_settings("https://as.example.invalid")

    loopback_issues = {
        (issue.location, issue.type)
        for issue in validate_production_settings(loopback, "production")
    }
    placeholder_issues = validate_production_settings(placeholder, "production")

    assert ("client_credentials_issuer", "https_required_in_production") in loopback_issues
    assert [(issue.location, issue.type) for issue in placeholder_issues] == [
        ("client_credentials_issuer", "placeholder_host_not_allowed")
    ]


def test_preflight_rejects_a_private_key_file_that_violates_the_policy() -> None:
    with secure_key_dir() as key_dir:
        good = write_key(key_dir / "good.pem", pem(rsa_key()))
        exposed = write_key(key_dir / "exposed.pem", good.read_bytes(), mode=0o644)

        def machine(key_path: object) -> Settings:
            return _production_settings(
                auth_provider="generic",
                auth_mode="client_credentials",
                client_auth_method="private_key_jwt",
                client_credentials_client_id="machine-client",
                client_credentials_private_key_path=key_path,
                client_credentials_issuer="https://login.acme.com",
            )

        assert validate_signing_key(machine(good)) == []
        issues = validate_signing_key(machine(exposed))

    assert [(issue.location, issue.type) for issue in issues] == [
        ("client_credentials_private_key_path", "private_key_rejected")
    ]


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
