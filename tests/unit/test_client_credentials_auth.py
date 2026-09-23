"""Tests for the non-interactive OAuth Client Credentials profile."""

from pathlib import Path

import httpx2
import pytest
from mcp.client.auth.extensions.client_credentials import (
    ClientCredentialsOAuthProvider,
    PrivateKeyJWTOAuthProvider,
)
from pydantic import ValidationError

from mcp_client_auth_template.adapters.client_credentials_auth import (
    OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID,
)
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.demo_client import (
    build_mcp_client,
    build_oauth_provider,
    build_token_storage,
)
from mcp_client_auth_template.entrypoints.settings import Settings
from tests.key_material import pem, rsa_key, secure_key_dir, write_key

_TEST_CLIENT_ID = "e2e-machine-client"
_TEST_CREDENTIAL = "unit-test-credential"
_TEST_ISSUER = "https://as.example.invalid"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_provider": "generic",
        "auth_mode": "client_credentials",
        "server_url": "https://mcp.example.invalid",
        "client_credentials_client_id": _TEST_CLIENT_ID,
        "client_credentials_secret": _TEST_CREDENTIAL,
        "client_credentials_issuer": _TEST_ISSUER,
        **overrides,
    }
    return Settings.model_validate(values)


def test_client_credentials_requires_pre_registered_credentials() -> None:
    with pytest.raises(ValidationError, match="client_credentials_client_id"):
        Settings(
            auth_provider="generic",
            auth_mode="client_credentials",
            server_url="https://mcp.example.invalid",
        )


def test_client_credentials_rejects_entra_profile() -> None:
    with pytest.raises(ValidationError, match="supports auth_provider=generic only"):
        Settings(
            auth_provider="entra",
            auth_mode="client_credentials",
            server_url="https://mcp.example.invalid",
            entra_tenant_id="11111111-1111-1111-1111-111111111111",
            entra_client_id="22222222-2222-2222-2222-222222222222",
            client_credentials_client_id=_TEST_CLIENT_ID,
            client_credentials_secret=_TEST_CREDENTIAL,
        )


def test_client_credentials_rejects_interactive_client_metadata() -> None:
    with pytest.raises(ValidationError, match="generic_client_metadata_url is not used"):
        _settings(
            generic_client_metadata_url=(
                "https://client.example.invalid/oauth/client-metadata.json"
            )
        )


def test_client_credentials_are_redacted_and_tokens_are_memory_only(tmp_path: Path) -> None:
    settings = _settings(token_storage_path=tmp_path / "tokens.json")

    assert _TEST_CREDENTIAL not in repr(settings)
    assert settings.token_storage_path is None
    assert isinstance(build_token_storage(settings), InMemoryTokenStorage)


async def test_builds_the_sdk_client_credentials_provider_without_browser_handlers() -> None:
    provider = await build_oauth_provider(
        _settings(),
        storage=InMemoryTokenStorage(),
    )

    assert isinstance(provider, ClientCredentialsOAuthProvider)


async def test_machine_client_advertises_the_extension() -> None:
    settings = _settings()
    async with httpx2.AsyncClient() as http_client:
        client = build_mcp_client(settings, http_client=http_client)

    assert client.extensions is not None
    assert [extension.identifier for extension in client.extensions] == [
        OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID
    ]


def test_client_credentials_require_an_explicit_issuer() -> None:
    with pytest.raises(ValidationError, match="requires: client_credentials_issuer"):
        _settings(client_credentials_issuer=None)


@pytest.mark.parametrize(
    "issuer",
    [
        "",
        " https://as.example.invalid",
        "https://as.example.invalid ",
        "https://as.example.invalid/\ttenant",
        "as.example.invalid",
        "/relative/issuer",
        "https://",
        "ftp://as.example.invalid",
        "https://user:pass@as.example.invalid",
        "https://user@as.example.invalid",
        "https://as.example.invalid?tenant=a",
        "https://as.example.invalid?",
        "https://as.example.invalid#frag",
        "https://as.example.invalid#",
        "http://as.example.invalid",
    ],
)
def test_client_credentials_issuer_fails_closed_on_structural_defects(issuer: str) -> None:
    with pytest.raises(ValidationError, match="client_credentials_issuer"):
        _settings(client_credentials_issuer=issuer)


def test_loopback_http_issuer_requires_the_explicit_development_opt_in() -> None:
    with pytest.raises(ValidationError, match="oauth_allow_insecure_loopback"):
        _settings(
            server_url="http://127.0.0.1:8000",
            oauth_allow_insecure_loopback=False,
            client_credentials_issuer="http://127.0.0.1:9000",
        )
    with pytest.raises(ValidationError, match="loopback hosts"):
        _settings(
            server_url="http://127.0.0.1:8000",
            oauth_allow_insecure_loopback=True,
            client_credentials_issuer="http://10.0.0.1:9000",
        )

    settings = _settings(
        server_url="http://127.0.0.1:8000",
        oauth_allow_insecure_loopback=True,
        client_credentials_issuer="http://127.0.0.1:9000",
    )
    assert settings.client_credentials_issuer == "http://127.0.0.1:9000"


@pytest.mark.parametrize(
    "issuer",
    [
        "https://AS.Example.invalid",
        "https://as.example.invalid:443",
        "https://as.example.invalid/tenant/v2.0",
        "https://as.example.invalid/",
        "https://bücher.example.invalid",
    ],
)
def test_issuer_is_preserved_verbatim_and_matching_is_left_to_the_sdk(issuer: str) -> None:
    settings = _settings(client_credentials_issuer=issuer)

    assert settings.client_credentials_issuer == issuer


def test_issuer_is_rejected_outside_client_credentials_mode() -> None:
    with pytest.raises(ValidationError, match="only with auth_mode=client_credentials"):
        Settings(
            auth_provider="generic",
            server_url="https://mcp.example.invalid",
            client_credentials_issuer=_TEST_ISSUER,
        )


async def test_configured_issuer_reaches_the_sdk_provider_unchanged() -> None:
    provider = await build_oauth_provider(
        _settings(client_credentials_issuer="https://as.example.invalid/tenant"),
        storage=InMemoryTokenStorage(),
    )

    assert isinstance(provider, ClientCredentialsOAuthProvider)
    assert provider._issuer == "https://as.example.invalid/tenant"


def _key_settings(key_path: Path | None, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "client_auth_method": "private_key_jwt",
        "client_credentials_secret": None,
        "client_credentials_private_key_path": key_path,
    }
    return _settings(**{**values, **overrides})


def test_client_secret_basic_remains_the_default_machine_method() -> None:
    assert _settings().client_auth_method == "client_secret_basic"


def test_private_key_jwt_requires_a_key_file_and_rejects_a_shared_secret() -> None:
    with pytest.raises(ValidationError, match="requires: client_credentials_private_key_path"):
        _key_settings(None)
    with pytest.raises(ValidationError, match="client_credentials_secret is not used"):
        _key_settings(Path("/run/secrets/client.pem"), client_credentials_secret=_TEST_CREDENTIAL)
    with pytest.raises(ValidationError, match="absolute path"):
        _key_settings(Path("secrets/client.pem"))


def test_client_secret_basic_rejects_a_key_file() -> None:
    with pytest.raises(ValidationError, match="client_credentials_private_key_path is not used"):
        _settings(client_credentials_private_key_path=Path("/run/secrets/client.pem"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_auth_method": "private_key_jwt"},
        {"client_credentials_private_key_path": Path("/run/secrets/client.pem")},
    ],
)
def test_machine_key_settings_are_rejected_in_interactive_mode(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="only with auth_mode=client_credentials"):
        Settings.model_validate(
            {"auth_provider": "generic", "server_url": "https://mcp.example.invalid", **overrides}
        )


async def test_private_key_jwt_builds_the_sdk_provider_bound_to_the_issuer() -> None:
    with secure_key_dir() as key_dir:
        key_path = write_key(key_dir / "client.pem", pem(rsa_key()))
        provider = await build_oauth_provider(
            _key_settings(key_path), storage=InMemoryTokenStorage()
        )

    assert isinstance(provider, PrivateKeyJWTOAuthProvider)
    assert provider._issuer == _TEST_ISSUER
