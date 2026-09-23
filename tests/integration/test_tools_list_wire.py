"""``tools/list`` through the real SDK client against the real SDK server, over sockets."""

import pytest

from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.cli_failures import (
    ClientFailureCategory,
    RequiredToolUnavailableError,
    classify_failure,
)
from mcp_client_auth_template.entrypoints.demo_client import (
    build_oauth_provider,
    discover_visible_tools,
)
from mcp_client_auth_template.entrypoints.settings import Settings
from tests.integration.client_stack import production_client
from tests.integration.fakes import (
    REQUIRED_SCOPE,
    FakeAuthorizationServer,
    TokenLedger,
    bind_loopback,
    resource_server_app,
    serve,
)

pytestmark = pytest.mark.integration

_CLIENT_ID = "tools-list-client"
_SECRET = "tools-list-secret"


async def test_visible_tools_come_from_the_sdk_wire_result_and_fail_closed() -> None:
    ledger = TokenLedger()
    as_socket, rs_socket = bind_loopback(), bind_loopback()
    authorization_server = FakeAuthorizationServer(
        ledger=ledger, client_id=_CLIENT_ID, client_secret=_SECRET, issuer=as_socket.url
    )
    async with (
        serve(authorization_server.app(), as_socket),
        serve(
            resource_server_app(
                resource_url=rs_socket.url, authorization_server=as_socket.url, ledger=ledger
            ),
            rs_socket,
        ) as resource,
    ):
        settings = Settings(
            auth_provider="generic",
            auth_mode="client_credentials",
            server_url=resource.url,
            scope=REQUIRED_SCOPE,
            oauth_allow_insecure_loopback=True,
            client_credentials_client_id=_CLIENT_ID,
            client_credentials_secret=_SECRET,
            client_credentials_issuer=as_socket.url,
        )
        provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())
        async with production_client(settings, oauth_provider=provider) as client:
            assert await discover_visible_tools(
                client, required=frozenset({"whoami"})
            ) == frozenset({"whoami"})
            with pytest.raises(RequiredToolUnavailableError) as raised:
                await discover_visible_tools(client, required=frozenset({"whoami", "health"}))

        assert raised.value.tool_name == "health"
        assert classify_failure(raised.value).category is ClientFailureCategory.TOOL
        tools_list_bodies = [
            request.body
            for request in resource.requests_to("/mcp")
            if b'"tools/list"' in request.body
        ]
        assert tools_list_bodies, "tools/list must have crossed the wire"
