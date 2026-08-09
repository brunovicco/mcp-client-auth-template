"""Tests for the non-interactive OAuth Client Credentials profile."""

from pathlib import Path

import httpx2
import pytest
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
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

_TEST_CLIENT_ID = "e2e-machine-client"
_TEST_CREDENTIAL = "unit-test-credential"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_provider": "generic",
        "auth_mode": "client_credentials",
        "server_url": "https://mcp.example.invalid",
        "client_credentials_client_id": _TEST_CLIENT_ID,
        "client_credentials_secret": _TEST_CREDENTIAL,
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
