"""MCP SDK 2.2 OAuth fail-closed regression suite, over the real wire.

Each scenario runs the client through two stacks:

- ``sdk``: the SDK's OAuth provider on a plain ``httpx2.AsyncClient``, proving the SDK boundary
  holds by itself;
- ``production``: the demo entrypoint's DNS-pinned, redirect-bounded transport, proving the
  composed client holds too.

A protection that lives in only one layer therefore cannot hide behind the other. Evidence
comes from request journals kept by local servers: what did or did not cross the socket.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx2
import jwt
import pytest
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult
from starlette.responses import JSONResponse, RedirectResponse, Response

from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.cli_failures import (
    ClientFailureCategory,
    classify_failure,
)
from mcp_client_auth_template.entrypoints.demo_client import build_mcp_client, build_oauth_provider
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
from tests.key_material import pem, rsa_key, secure_key_dir, write_key

pytestmark = pytest.mark.integration

Mode = Literal["interactive", "client_secret_basic", "private_key_jwt"]
Stack = Literal["sdk", "production"]

_CLIENT_ID = "fail-closed-client"
_SECRET = "fail-closed-secret-51c0de"
_PRM_PATH = "/.well-known/oauth-protected-resource"
_AS_METADATA_PATHS = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
)
_MACHINE_MODES: tuple[Mode, ...] = ("client_secret_basic", "private_key_jwt")
_ALL_MODES: tuple[Mode, ...] = ("interactive", *_MACHINE_MODES)
_STACKS: tuple[Stack, ...] = ("sdk", "production")


@dataclass
class _World:
    """Trusted AS, an unrelated ``elsewhere`` origin, and the real SDK resource server."""

    trusted: RunningServer
    elsewhere: RunningServer
    resource: RunningServer
    key_path: Path
    key_pem: str
    browser_opened: list[str] = field(default_factory=list)
    signed_assertions: list[str] = field(default_factory=list)

    def journals(self) -> list[list[RecordedRequest]]:
        return [self.trusted.journal, self.elsewhere.journal, self.resource.journal]

    def credential_sentinels(self) -> list[str]:
        body = [line for line in self.key_pem.splitlines() if not line.startswith("-----")]
        return [_SECRET, self.key_pem, *body]


@pytest.fixture
def key_file() -> Iterator[tuple[Path, Any]]:
    key = rsa_key()
    with secure_key_dir() as key_dir:
        yield write_key(key_dir / "client.pem", pem(key)), key.public_key()


@asynccontextmanager
async def _world(key_file: tuple[Path, Any]) -> AsyncIterator[_World]:
    key_path, public_key = key_file
    ledger = TokenLedger()
    trusted_socket, elsewhere_socket, resource_socket = (
        bind_loopback(),
        bind_loopback(),
        bind_loopback(),
    )
    signed: list[str] = []

    async def verify(assertion: str, audience: str) -> str | None:
        signed.append(assertion)
        try:
            claims = jwt.decode(
                assertion, public_key, algorithms=["RS256"], audience=audience, issuer=_CLIENT_ID
            )
        except jwt.PyJWTError:
            return None
        return str(claims["sub"])

    trusted_as = FakeAuthorizationServer(
        ledger=ledger,
        client_id=_CLIENT_ID,
        client_secret=_SECRET,
        assertion_verifier=verify,
        issuer=trusted_socket.url,
    )
    # A second origin that nothing is supposed to talk to; it serves AS endpoints so that a
    # leak would succeed rather than fail noisily.
    elsewhere_as = FakeAuthorizationServer(
        ledger=ledger,
        client_id=_CLIENT_ID,
        client_secret=_SECRET,
        assertion_verifier=verify,
        issuer=elsewhere_socket.url,
    )
    async with (
        serve(trusted_as.app(), trusted_socket) as trusted,
        serve(elsewhere_as.app(), elsewhere_socket) as elsewhere,
        serve(
            resource_server_app(
                resource_url=resource_socket.url,
                authorization_server=trusted_socket.url,
                ledger=ledger,
            ),
            resource_socket,
        ) as resource,
    ):
        yield _World(
            trusted=trusted,
            elsewhere=elsewhere,
            resource=resource,
            key_path=key_path,
            key_pem=key_path.read_text(),
            signed_assertions=signed,
        )


def _settings(world: _World, mode: Mode) -> Settings:
    common: dict[str, object] = {
        "auth_provider": "generic",
        "server_url": world.resource.url,
        "scope": REQUIRED_SCOPE,
        "oauth_allow_insecure_loopback": True,
        "token_storage_path": None,
    }
    if mode == "interactive":
        return Settings.model_validate(common)
    machine: dict[str, object] = {
        **common,
        "auth_mode": "client_credentials",
        "client_auth_method": mode,
        "client_credentials_client_id": _CLIENT_ID,
        "client_credentials_issuer": world.trusted.url,
    }
    if mode == "client_secret_basic":
        machine["client_credentials_secret"] = _SECRET
    else:
        machine["client_credentials_private_key_path"] = world.key_path
    return Settings.model_validate(machine)


async def _provider(world: _World, settings: Settings) -> OAuthClientProvider:
    async def open_browser(url: str) -> None:
        world.browser_opened.append(url)

    async def no_callback() -> AuthorizationCodeResult:  # pragma: no cover - must never run
        raise AssertionError("the authorization callback must never be awaited here")

    if settings.auth_mode == "interactive":
        return await build_oauth_provider(
            settings,
            storage=InMemoryTokenStorage(),
            redirect_handler=open_browser,
            callback_handler=no_callback,
        )
    return await build_oauth_provider(settings, storage=InMemoryTokenStorage())


@asynccontextmanager
async def _sdk_client(
    settings: Settings, *, oauth_provider: OAuthClientProvider
) -> AsyncIterator[Client]:
    async with (
        httpx2.AsyncClient(auth=oauth_provider, follow_redirects=True, timeout=10.0) as http_client,
        build_mcp_client(settings, http_client=http_client) as client,
    ):
        yield client


_ClientFactory = Callable[..., AbstractAsyncContextManager[Client]]
_FACTORIES: dict[Stack, _ClientFactory] = {"sdk": _sdk_client, "production": production_client}


async def _connect_and_call(world: _World, mode: Mode, stack: Stack) -> Exception | None:
    """Connect and call ``whoami``; return the failure instead of raising it."""
    settings = _settings(world, mode)
    provider = await _provider(world, settings)
    try:
        async with _FACTORIES[stack](settings, oauth_provider=provider) as client:
            await client.call_tool("whoami")
    except Exception as exc:
        return exc
    return None


def _leaf_exceptions(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for nested in error.exceptions for leaf in _leaf_exceptions(nested)]
    return [error]


def _assert_nothing_sensitive_reached(world: _World, *journals: list[RecordedRequest]) -> None:
    for journal in journals:
        for request in journal:
            assert request.header("authorization") is None, f"credential sent to {request.path}"
            assert "client_assertion" not in request.form()
    assert_never_seen(list(journals), *world.credential_sentinels())


def _assert_no_legacy_fallback(world: _World) -> None:
    """Nothing beyond PRM discovery happened on the resource origin or any AS."""
    resource_paths = {request.path for request in world.resource.journal}
    assert resource_paths <= {"/mcp", _PRM_PATH}
    assert world.trusted.journal == []
    assert world.elsewhere.journal == []
    assert world.browser_opened == []


# --- Protected Resource Metadata failures -------------------------------------------------


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _ALL_MODES)
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_prm_server_errors_fail_closed_without_legacy_fallback(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack, status: int
) -> None:
    async with _world(key_file) as world:
        world.resource.override(_PRM_PATH, lambda _: Response(status_code=status))

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert any(f"HTTP {status}" in str(leaf) for leaf in _leaf_exceptions(failure)), failure
        assert classify_failure(failure).category is ClientFailureCategory.AUTHENTICATION
        _assert_no_legacy_fallback(world)


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _ALL_MODES)
async def test_prm_rate_limit_then_not_found_still_fails_closed(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    """A 429 on the advertised URL is not erased by a later candidate's 404."""
    async with _world(key_file) as world:
        answers = iter([429, 404])
        world.resource.override(_PRM_PATH, lambda _: Response(status_code=next(answers, 404)))

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert any("HTTP 429" in str(leaf) for leaf in _leaf_exceptions(failure)), failure
        _assert_no_legacy_fallback(world)


# --- Authorization-server metadata issuer ---------------------------------------------------


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _ALL_MODES)
async def test_as_metadata_issuer_mismatch_fails_closed_before_registration_or_token(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    async with _world(key_file) as world:
        impostor = f"{world.elsewhere.url}"
        trusted_url = world.trusted.url

        def metadata(_: RecordedRequest) -> Response:
            return JSONResponse(
                {
                    "issuer": impostor,
                    "authorization_endpoint": f"{trusted_url}/authorize",
                    "token_endpoint": f"{trusted_url}/token",
                    "registration_endpoint": f"{trusted_url}/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                }
            )

        for path in _AS_METADATA_PATHS:
            world.trusted.override(path, metadata)

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert any("issuer mismatch" in str(leaf) for leaf in _leaf_exceptions(failure)), failure
        assert {request.path for request in world.trusted.journal} <= set(_AS_METADATA_PATHS)
        assert world.elsewhere.journal == []
        assert world.browser_opened == []
        assert world.signed_assertions == []
        _assert_nothing_sensitive_reached(world, world.trusted.journal, world.elsewhere.journal)


# --- Cross-origin redirects -------------------------------------------------------------------


def _redirect_to(server: RunningServer, path: str, status: int = 307) -> Callable[..., Response]:
    return lambda _: RedirectResponse(f"{server.url}{path}", status_code=status)


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _ALL_MODES)
async def test_cross_origin_prm_redirect_is_not_followed(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    async with _world(key_file) as world:
        world.resource.override(_PRM_PATH, _redirect_to(world.elsewhere, _PRM_PATH, status=302))

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert world.elsewhere.journal == []
        assert world.browser_opened == []
        _assert_nothing_sensitive_reached(world, *world.journals())


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _ALL_MODES)
async def test_cross_origin_as_metadata_redirect_is_not_followed(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    async with _world(key_file) as world:
        for path in _AS_METADATA_PATHS:
            world.trusted.override(path, _redirect_to(world.elsewhere, path))

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert world.elsewhere.journal == []
        assert world.trusted.requests_to("/token") == []
        assert world.browser_opened == []
        _assert_nothing_sensitive_reached(world, *world.journals())


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _MACHINE_MODES)
async def test_token_endpoint_redirect_never_replays_client_authentication_elsewhere(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    async with _world(key_file) as world:
        world.trusted.override("/token", _redirect_to(world.elsewhere, "/token"))

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert world.elsewhere.journal == []
        assert len(world.trusted.requests_to("/token")) == 1
        _assert_nothing_sensitive_reached(world, world.elsewhere.journal, world.resource.journal)


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _MACHINE_MODES)
async def test_mcp_endpoint_redirect_never_carries_the_bearer_token_elsewhere(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    async with _world(key_file) as world:
        redirect = _redirect_to(world.elsewhere, "/mcp")

        def bounce_authenticated(request: RecordedRequest) -> Response | None:
            if (request.header("authorization") or "").lower().startswith("bearer "):
                return redirect(request)
            return None

        world.resource.override("/mcp", bounce_authenticated)

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert world.elsewhere.journal == []
        assert world.trusted.requests_to("/token"), "the token was acquired before the redirect"


# --- 403 handling ------------------------------------------------------------------------------


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _MACHINE_MODES)
@pytest.mark.parametrize(
    "challenge",
    [
        'Bearer error="invalid_token", error_description="revoked"',
        'Bearer error="access_denied"',
        None,
    ],
)
async def test_403_without_insufficient_scope_does_not_restart_authorization(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack, challenge: str | None
) -> None:
    async with _world(key_file) as world:

        def forbid(request: RecordedRequest) -> Response | None:
            if not (request.header("authorization") or "").lower().startswith("bearer "):
                return None
            headers = {"WWW-Authenticate": challenge} if challenge is not None else {}
            return JSONResponse({"error": "forbidden"}, status_code=403, headers=headers)

        world.resource.override("/mcp", forbid)

        failure = await _connect_and_call(world, mode, stack)

        assert failure is not None
        assert len(world.trusted.requests_to("/token")) == 1
        assert len(world.resource.requests_to(_PRM_PATH)) == 1
        assert classify_failure(failure).category is not ClientFailureCategory.INTERNAL


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _MACHINE_MODES)
async def test_403_insufficient_scope_triggers_exactly_one_step_up(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    """Positive control: the only 403 that re-authorizes is an ``insufficient_scope`` challenge."""
    async with _world(key_file) as world:
        extra = "mcp:tools:extra"
        challenged: list[bool] = []

        def challenge_once(request: RecordedRequest) -> Response | None:
            # Challenge the tool call itself, as a resource server enforcing per-tool scopes
            # does; the handshake before it succeeds with the initial grant.
            if challenged or b'"tools/call"' not in request.body:
                return None
            challenged.append(True)
            return JSONResponse(
                {"error": "insufficient_scope"},
                status_code=403,
                headers={
                    "WWW-Authenticate": (
                        f'Bearer error="insufficient_scope", scope="{REQUIRED_SCOPE} {extra}"'
                    )
                },
            )

        world.resource.override("/mcp", challenge_once)

        failure = await _connect_and_call(world, mode, stack)

        assert failure is None
        token_requests = world.trusted.requests_to("/token")
        assert len(token_requests) == 2
        assert set(token_requests[1].form()["scope"].split()) == {REQUIRED_SCOPE, extra}


# --- Credential forwarding ---------------------------------------------------------------------


@pytest.mark.parametrize("stack", _STACKS)
@pytest.mark.parametrize("mode", _MACHINE_MODES)
async def test_each_credential_stays_inside_its_own_boundary(
    key_file: tuple[Path, Any], mode: Mode, stack: Stack
) -> None:
    """Secret and assertion only at the bound token endpoint, key nowhere, bearer only at /mcp."""
    async with _world(key_file) as world:
        failure = await _connect_and_call(world, mode, stack)
        assert failure is None

        everything = [
            (name, request)
            for name, server in (
                ("trusted", world.trusted),
                ("elsewhere", world.elsewhere),
                ("resource", world.resource),
            )
            for request in server.journal
        ]
        client_authenticated = [
            (name, request.path)
            for name, request in everything
            if _SECRET.encode() in request.raw() or "client_assertion" in request.form()
        ]
        bearer_carrying = {
            (name, request.path)
            for name, request in everything
            if (request.header("authorization") or "").lower().startswith("bearer ")
        }

        assert client_authenticated == [("trusted", "/token")]
        assert bearer_carrying == {("resource", "/mcp")}
        assert world.elsewhere.journal == []
        assert_never_seen(world.journals(), world.key_pem, *world.key_pem.splitlines()[1:-1])
