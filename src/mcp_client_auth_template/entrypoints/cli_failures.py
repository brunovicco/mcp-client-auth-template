"""Stable, secret-free operational failure contract for the interactive client."""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum, StrEnum

import anyio
import httpx2
import structlog
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.exceptions import MCPError

from mcp_client_auth_template.adapters.oauth_discovery_security import OAuthNetworkSecurityError
from mcp_client_auth_template.adapters.token_storage import (
    TokenStorageCorruptionError,
    TokenStorageSecurityError,
)
from mcp_client_auth_template.entrypoints.preflight import ConfigurationPreflightError

logger = structlog.get_logger(__name__)


class ClientExitCode(IntEnum):
    """Stable process exit codes for automation around the demo client."""

    SUCCESS = 0
    CONFIGURATION = 2
    AUTHENTICATION = 3
    NETWORK = 4
    TIMEOUT = 5
    LOCAL_STORAGE = 6
    TOOL = 7
    MCP_PROTOCOL = 8
    INTERNAL = 70
    INTERRUPTED = 130


class ClientFailureCategory(StrEnum):
    """Stable, low-cardinality failure categories safe for logs and automation."""

    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    TIMEOUT = "timeout"
    LOCAL_STORAGE = "local_storage"
    TOOL = "tool"
    MCP_PROTOCOL = "mcp_protocol"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ClientFailure:
    """Sanitized classification of one operational exception."""

    category: ClientFailureCategory
    exit_code: ClientExitCode
    exception_type: str


class ToolCallFailedError(RuntimeError):
    """Raised when an MCP tool returns ``is_error=True``."""

    def __init__(self, tool_name: str) -> None:
        """Remember only the safe tool name; never retain response content."""
        self.tool_name = tool_name
        super().__init__("MCP tool returned an error result")


def _exception_tree(error: Exception) -> Iterator[Exception]:
    """Yield an exception and any nested ``ExceptionGroup`` members."""
    yield error
    if isinstance(error, ExceptionGroup):
        for nested in error.exceptions:
            yield from _exception_tree(nested)


def _first_matching(
    errors: tuple[Exception, ...],
    error_types: tuple[type[Exception], ...],
) -> Exception | None:
    return next((error for error in errors if isinstance(error, error_types)), None)


def _failure(
    error: Exception,
    category: ClientFailureCategory,
    exit_code: ClientExitCode,
) -> ClientFailure:
    return ClientFailure(
        category=category,
        exit_code=exit_code,
        exception_type=type(error).__name__,
    )


def classify_failure(error: Exception) -> ClientFailure:
    """Map expected operational exceptions to a stable, secret-free process contract."""
    errors = tuple(_exception_tree(error))

    matched = _first_matching(errors, (ConfigurationPreflightError,))
    if matched is not None:
        return _failure(matched, ClientFailureCategory.CONFIGURATION, ClientExitCode.CONFIGURATION)

    matched = _first_matching(errors, (TokenStorageSecurityError, TokenStorageCorruptionError))
    if matched is not None:
        return _failure(matched, ClientFailureCategory.LOCAL_STORAGE, ClientExitCode.LOCAL_STORAGE)

    matched = _first_matching(errors, (OAuthFlowError,))
    if matched is not None:
        return _failure(
            matched, ClientFailureCategory.AUTHENTICATION, ClientExitCode.AUTHENTICATION
        )

    matched = _first_matching(errors, (TimeoutError,))
    if matched is not None:
        return _failure(matched, ClientFailureCategory.TIMEOUT, ClientExitCode.TIMEOUT)

    matched = _first_matching(
        errors,
        (
            OAuthNetworkSecurityError,
            httpx2.RequestError,
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
            anyio.EndOfStream,
        ),
    )
    if matched is not None:
        return _failure(matched, ClientFailureCategory.NETWORK, ClientExitCode.NETWORK)

    matched = _first_matching(errors, (ToolCallFailedError,))
    if matched is not None:
        return _failure(matched, ClientFailureCategory.TOOL, ClientExitCode.TOOL)

    matched = _first_matching(errors, (MCPError,))
    if matched is not None:
        return _failure(matched, ClientFailureCategory.MCP_PROTOCOL, ClientExitCode.MCP_PROTOCOL)

    return _failure(error, ClientFailureCategory.INTERNAL, ClientExitCode.INTERNAL)


def emit_failure(failure: ClientFailure) -> None:
    """Emit only allowlisted failure metadata; never log exception messages or payloads."""
    logger.error(
        "mcp_client_failed",
        category=failure.category.value,
        exit_code=int(failure.exit_code),
        exception_type=failure.exception_type,
    )
