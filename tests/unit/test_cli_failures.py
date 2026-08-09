"""Stable process failure contract tests."""

import anyio
import pytest
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.exceptions import MCPError

from mcp_client_auth_template.adapters.oauth_discovery_security import OAuthNetworkSecurityError
from mcp_client_auth_template.adapters.token_storage import (
    TokenStorageCorruptionError,
    TokenStorageSecurityError,
)
from mcp_client_auth_template.entrypoints import cli_failures, demo_client
from mcp_client_auth_template.entrypoints.cli_failures import (
    ClientExitCode,
    ClientFailureCategory,
    ToolCallFailedError,
    classify_failure,
    emit_failure,
)
from mcp_client_auth_template.entrypoints.preflight import ConfigurationPreflightError


@pytest.mark.parametrize(
    ("error", "category", "exit_code"),
    [
        (
            ConfigurationPreflightError("sensitive-config-value"),
            ClientFailureCategory.CONFIGURATION,
            ClientExitCode.CONFIGURATION,
        ),
        (
            OAuthFlowError("sensitive-oauth-value"),
            ClientFailureCategory.AUTHENTICATION,
            ClientExitCode.AUTHENTICATION,
        ),
        (
            OAuthNetworkSecurityError("sensitive-network-value"),
            ClientFailureCategory.NETWORK,
            ClientExitCode.NETWORK,
        ),
        (
            TimeoutError("sensitive-timeout-value"),
            ClientFailureCategory.TIMEOUT,
            ClientExitCode.TIMEOUT,
        ),
        (
            TokenStorageSecurityError("sensitive-storage-value"),
            ClientFailureCategory.LOCAL_STORAGE,
            ClientExitCode.LOCAL_STORAGE,
        ),
        (
            TokenStorageCorruptionError("sensitive-storage-value"),
            ClientFailureCategory.LOCAL_STORAGE,
            ClientExitCode.LOCAL_STORAGE,
        ),
        (ToolCallFailedError("whoami"), ClientFailureCategory.TOOL, ClientExitCode.TOOL),
        (
            MCPError(-32603, "sensitive-peer-value"),
            ClientFailureCategory.MCP_PROTOCOL,
            ClientExitCode.MCP_PROTOCOL,
        ),
        (
            RuntimeError("sensitive-internal-value"),
            ClientFailureCategory.INTERNAL,
            ClientExitCode.INTERNAL,
        ),
    ],
)
def test_classify_failure_uses_stable_categories(
    error: Exception,
    category: ClientFailureCategory,
    exit_code: ClientExitCode,
) -> None:
    failure = classify_failure(error)

    assert failure.category is category
    assert failure.exit_code is exit_code
    assert failure.exception_type == type(error).__name__


def test_exception_group_preserves_known_failure_classification() -> None:
    error = ExceptionGroup(
        "outer",
        [RuntimeError("internal"), OAuthNetworkSecurityError("network-secret")],
    )

    failure = classify_failure(error)

    assert failure.category is ClientFailureCategory.NETWORK
    assert failure.exit_code is ClientExitCode.NETWORK
    assert failure.exception_type == "OAuthNetworkSecurityError"


class _CapturedLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def error(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def test_failure_log_never_contains_exception_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _CapturedLogger()
    monkeypatch.setattr(cli_failures, "logger", captured)
    secret = "Bearer ultra-sensitive-value"

    emit_failure(classify_failure(OAuthFlowError(secret)))

    event, fields = captured.events[0]
    assert event == "mcp_client_failed"
    assert fields == {
        "category": "authentication",
        "exit_code": 3,
        "exception_type": "OAuthFlowError",
    }
    assert secret not in repr(fields)


def test_run_cli_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anyio, "run", lambda _function: None)

    assert demo_client.run_cli() is ClientExitCode.SUCCESS


def test_run_cli_classifies_expected_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_function: object) -> None:
        raise OAuthFlowError("do-not-log-this")

    captured: list[ClientFailureCategory] = []
    monkeypatch.setattr(anyio, "run", fail)
    monkeypatch.setattr(
        demo_client,
        "emit_failure",
        lambda failure: captured.append(failure.category),
    )

    assert demo_client.run_cli() is ClientExitCode.AUTHENTICATION
    assert captured == [ClientFailureCategory.AUTHENTICATION]


def test_run_cli_maps_keyboard_interrupt_to_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_function: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(anyio, "run", interrupt)

    assert demo_client.run_cli() is ClientExitCode.INTERRUPTED
