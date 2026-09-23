"""MCP SDK deprecations fail the suite so MCP 3 breakage surfaces on MCP 2.x."""

import warnings

import pytest
from mcp import MCPDeprecationWarning
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider

from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage


def test_pytest_promotes_mcp_deprecations_to_errors() -> None:
    """``filterwarnings`` in ``pyproject.toml`` turns SDK deprecations into failures."""
    with pytest.raises(MCPDeprecationWarning):
        warnings.warn("deprecated MCP API", MCPDeprecationWarning, stacklevel=1)


def test_an_issuer_less_machine_provider_cannot_pass_ci() -> None:
    """Omitting ``issuer=`` (ADR-0024) is an SDK deprecation, so it fails rather than warns."""
    with pytest.raises(MCPDeprecationWarning, match="issuer"):
        ClientCredentialsOAuthProvider(
            server_url="https://mcp.example.invalid",
            storage=InMemoryTokenStorage(),
            client_id="machine-client",
            client_secret="unit-test-credential",
        )


def test_only_mcp_deprecations_are_promoted(pytestconfig: pytest.Config) -> None:
    """Only the SDK's own warning class is escalated; other deprecations need evaluation first."""
    assert pytestconfig.getini("filterwarnings") == ["error::mcp.MCPDeprecationWarning"]
    with pytest.warns(DeprecationWarning):
        warnings.warn("third-party deprecation", DeprecationWarning, stacklevel=1)
