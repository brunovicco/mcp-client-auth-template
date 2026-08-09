"""Operational budget and cancellation tests for the demo client."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import cast

import anyio
import pytest
from mcp.client import Client
from mcp.types import CallToolResult, TextContent

from mcp_client_auth_template.entrypoints.cli_failures import ToolCallFailedError
from mcp_client_auth_template.entrypoints.demo_client import (
    build_http_timeout,
    call_tool_with_budget,
    close_with_budget,
)
from mcp_client_auth_template.entrypoints.settings import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_provider": "generic",
        "server_url": "https://mcp.example.invalid",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_http_timeout_uses_distinct_budgets() -> None:
    timeout = build_http_timeout(
        _settings(
            http_connect_timeout_seconds=1.0,
            http_read_timeout_seconds=2.0,
            http_write_timeout_seconds=3.0,
            http_pool_timeout_seconds=4.0,
        )
    )

    assert timeout.connect == 1.0
    assert timeout.read == 2.0
    assert timeout.write == 3.0
    assert timeout.pool == 4.0


class _BlockingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, _tool_name: str) -> CallToolResult:
        self.calls += 1
        await anyio.sleep_forever()
        raise AssertionError("unreachable")


async def test_tool_timeout_cancels_once_without_retrying() -> None:
    fake = _BlockingClient()

    with pytest.raises(TimeoutError):
        await call_tool_with_budget(
            cast(Client, fake),
            "whoami",
            timeout_seconds=0.01,
        )

    assert fake.calls == 1


async def test_close_with_budget_reports_expired_cleanup() -> None:
    exited = anyio.Event()

    @asynccontextmanager
    async def slow_resource() -> AsyncIterator[None]:
        try:
            yield None
        finally:
            try:
                await anyio.sleep_forever()
            finally:
                exited.set()

    stack = AsyncExitStack()
    await stack.enter_async_context(slow_resource())

    timed_out = await close_with_budget(stack, timeout_seconds=0.01)

    assert timed_out is True
    assert exited.is_set()


class _ErrorResultClient:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, _tool_name: str) -> CallToolResult:
        self.calls += 1
        return CallToolResult(
            content=[TextContent(type="text", text="sensitive tool failure payload")],
            is_error=True,
        )


async def test_tool_error_result_fails_once_without_retaining_payload() -> None:
    fake = _ErrorResultClient()

    with pytest.raises(ToolCallFailedError) as exc_info:
        await call_tool_with_budget(
            cast(Client, fake),
            "whoami",
            timeout_seconds=1.0,
        )

    assert fake.calls == 1
    assert exc_info.value.tool_name == "whoami"
    assert "sensitive tool failure payload" not in str(exc_info.value)
