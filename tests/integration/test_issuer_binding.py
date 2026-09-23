"""Issuer binding for pre-provisioned machine credentials, proven over the real wire (ADR-0024).

The client is composed exactly as the demo entrypoint composes it, the resource server is the
MCP SDK's own server, and every authorization server journals what it received. Assertions are
about bytes that crossed the socket, never about SDK objects built by hand.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from mcp.client.auth import OAuthFlowError
from starlette.responses import JSONResponse, Response

from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.demo_client import build_oauth_provider
from mcp_client_auth_template.entrypoints.settings import Settings
from tests.integration.client_stack import production_client
from tests.integration.fakes import (
    REQUIRED_SCOPE,
    FakeAuthorizationServer,
    RecordedRequest,
    RunningServer,
    TokenLedger,
    assert_never_seen,
    bind_loopback,
    resource_server_app,
    serve,
)

pytestmark = pytest.mark.integration

_CLIENT_ID = "integration-machine-client"
_SECRET = "integration-secret-7f3c9d2e"
_PRM_PATH = "/.well-known/oauth-protected-resource"


@dataclass
class _Topology:
    trusted: RunningServer
    rogue: RunningServer
    resource: RunningServer
    trusted_as: FakeAuthorizationServer
    ledger: TokenLedger


@asynccontextmanager
async def _topology(*, advertised: str) -> AsyncIterator[_Topology]:
    """Start trusted AS ``A``, rogue AS ``B`` and a real SDK RS whose PRM names ``advertised``."""
    ledger = TokenLedger()
    trusted_socket, rogue_socket, resource_socket = (
        bind_loopback(),
        bind_loopback(),
        bind_loopback(),
    )
    trusted_as = FakeAuthorizationServer(
        ledger=ledger, client_id=_CLIENT_ID, client_secret=_SECRET, issuer=trusted_socket.url
    )
    # The rogue server would happily accept anything; it must simply never be sent anything.
    rogue_as = FakeAuthorizationServer(
        ledger=ledger, client_id=_CLIENT_ID, client_secret=_SECRET, issuer=rogue_socket.url
    )
    authorization_server = {"trusted": trusted_socket.url, "rogue": rogue_socket.url}[advertised]
    async with (
        serve(trusted_as.app(), trusted_socket) as trusted,
        serve(rogue_as.app(), rogue_socket) as rogue,
        serve(
            resource_server_app(
                resource_url=resource_socket.url,
                authorization_server=authorization_server,
                ledger=ledger,
            ),
            resource_socket,
        ) as resource,
    ):
        yield _Topology(
            trusted=trusted,
            rogue=rogue,
            resource=resource,
            trusted_as=trusted_as,
            ledger=ledger,
        )


def _settings(topology: _Topology) -> Settings:
    return Settings(
        auth_provider="generic",
        auth_mode="client_credentials",
        server_url=topology.resource.url,
        scope=REQUIRED_SCOPE,
        oauth_allow_insecure_loopback=True,
        client_credentials_client_id=_CLIENT_ID,
        client_credentials_secret=_SECRET,
        client_credentials_issuer=topology.trusted.url,
    )


def _bearer_requests(journal: list[RecordedRequest]) -> list[RecordedRequest]:
    return [
        request
        for request in journal
        if (request.header("authorization") or "").lower().startswith("bearer ")
    ]


def _assert_received_no_credential(journal: list[RecordedRequest]) -> None:
    for request in journal:
        assert request.method == "GET", f"unexpected {request.method} {request.path}"
        assert request.header("authorization") is None
        assert request.path != "/token"
        assert not {"client_id", "client_secret", "client_assertion"} & set(request.form())
    assert_never_seen([journal], _SECRET, _CLIENT_ID)


async def test_credential_never_reaches_an_authorization_server_the_prm_substitutes() -> None:
    """Configured issuer A, PRM advertises B: abort before any credential leaves the process."""
    async with _topology(advertised="rogue") as topology:
        settings = _settings(topology)
        provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())

        with pytest.RaisesGroup(
            pytest.RaisesExc(OAuthFlowError, match="metadata issuer mismatch"),
            allow_unwrapped=True,
            flatten_subgroups=True,
        ):
            async with production_client(settings, oauth_provider=provider):
                pytest.fail("the client connected through an unexpected authorization server")

        # B's public metadata was fetched (unauthenticated) and rejected on its issuer.
        assert topology.rogue.requests_to("/.well-known/oauth-authorization-server")
        _assert_received_no_credential(topology.rogue.journal)
        assert topology.trusted.journal == []
        assert _bearer_requests(topology.resource.journal) == []
        assert topology.ledger.issued == {}
        assert_never_seen([topology.resource.journal], _SECRET)


async def test_sdk_selects_the_configured_issuer_when_the_prm_lists_several() -> None:
    """PRM advertises ``[B, A]``: the configured issuer wins and B receives nothing at all."""
    async with _topology(advertised="rogue") as topology:
        resource_url = topology.resource.url
        rogue_url, trusted_url = topology.rogue.url, topology.trusted.url

        def prm(_: RecordedRequest) -> Response:
            return JSONResponse(
                {
                    "resource": f"{resource_url}/",
                    "authorization_servers": [rogue_url, trusted_url],
                    "scopes_supported": [REQUIRED_SCOPE],
                }
            )

        topology.resource.override(_PRM_PATH, prm)
        settings = _settings(topology)
        provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())

        async with production_client(settings, oauth_provider=provider) as client:
            result = await client.call_tool("whoami")

        assert result.structured_content == {"client_id": _CLIENT_ID, "scopes": [REQUIRED_SCOPE]}
        assert topology.rogue.journal == []
        token_requests = topology.trusted.requests_to("/token")
        assert len(token_requests) == 1
        assert (token_requests[0].header("authorization") or "").startswith("Basic ")
        assert {request.path for request in _bearer_requests(topology.resource.journal)} == {"/mcp"}


async def test_metadata_naming_another_issuer_aborts_before_the_token_request() -> None:
    """A's metadata claims issuer A': fail closed with no token request anywhere."""
    async with _topology(advertised="trusted") as topology:
        topology.trusted_as.metadata_issuer = f"{topology.trusted.url}/impostor"
        settings = _settings(topology)
        provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())

        with pytest.RaisesGroup(
            pytest.RaisesExc(OAuthFlowError, match="issuer mismatch"),
            allow_unwrapped=True,
            flatten_subgroups=True,
        ):
            async with production_client(settings, oauth_provider=provider):
                pytest.fail("metadata for another issuer was accepted")

        assert topology.trusted.requests_to("/token") == []
        _assert_received_no_credential(topology.trusted.journal)
        assert topology.rogue.journal == []
        assert topology.ledger.issued == {}
