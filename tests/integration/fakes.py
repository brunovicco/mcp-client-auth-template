"""Deterministic local servers for real-wire OAuth/MCP integration tests.

Every server listens on an ephemeral loopback socket and journals each HTTP request it
receives (method, path, query, headers, raw body) before any application code runs. Tests
assert on those journals, i.e. on what actually crossed the wire, rather than on SDK objects
built by hand.

The resource server is the MCP SDK's own ``MCPServer`` Streamable HTTP app, so the client
talks to the real SDK server stack (PRM, bearer middleware, JSON-RPC dispatch). Individual
paths can be overridden to inject protocol failures (429/5xx, redirects, 403 variants).
"""

import base64
import secrets
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import anyio
import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUIRED_SCOPE = "mcp:tools:call"

Override = Callable[["RecordedRequest"], Response | None]


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One HTTP request exactly as it arrived on the socket."""

    method: str
    path: str
    query: str
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header(self, name: str) -> str | None:
        """Return the last value of a header (case-insensitive), if present."""
        lowered = name.lower()
        values = [value for key, value in self.headers if key == lowered]
        return values[-1] if values else None

    def form(self) -> dict[str, str]:
        """Decode an ``application/x-www-form-urlencoded`` body to single values."""
        parsed = parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}

    def raw(self) -> bytes:
        """Every byte of the request a credential could hide in, Basic auth decoded."""
        head = "\n".join(
            [f"{self.method} {self.path}?{self.query}"]
            + [f"{key}: {value}" for key, value in self.headers]
        )
        decoded = b""
        scheme, _, encoded = (self.header("authorization") or "").partition(" ")
        if scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(encoded)
            except ValueError:
                decoded = b""
        return head.encode("utf-8", "replace") + b"\n" + decoded + b"\n\n" + self.body


class _Recorder:
    """ASGI wrapper that journals each HTTP request and applies path overrides."""

    def __init__(self, app: ASGIApp, journal: list[RecordedRequest]) -> None:
        self._app = app
        self._journal = journal
        self.overrides: dict[str, Override] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        record = RecordedRequest(
            method=scope["method"],
            path=scope["path"],
            query=scope.get("query_string", b"").decode("latin-1"),
            headers=tuple(
                (key.decode("latin-1").lower(), value.decode("latin-1"))
                for key, value in scope["headers"]
            ),
            body=body,
        )
        self._journal.append(record)

        override = self.overrides.get(scope["path"])
        response = override(record) if override is not None else None
        if response is not None:
            await response(scope, receive, send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)


@dataclass
class RunningServer:
    """A live loopback server and its request journal."""

    url: str
    journal: list[RecordedRequest]
    recorder: _Recorder

    def override(self, path: str, responder: Override) -> None:
        """Answer ``path`` with ``responder`` (return ``None`` to pass through)."""
        self.recorder.overrides[path] = responder

    def requests_to(self, path: str) -> list[RecordedRequest]:
        """All journaled requests for one path."""
        return [request for request in self.journal if request.path == path]


@dataclass
class Loopback:
    """A pre-bound ephemeral loopback socket, so an app can know its own URL."""

    sock: socket.socket
    url: str


def bind_loopback() -> Loopback:
    """Bind ``127.0.0.1:0`` and return the socket plus its base URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return Loopback(sock=sock, url=f"http://127.0.0.1:{port}")


@asynccontextmanager
async def serve(app: ASGIApp, loopback: Loopback) -> AsyncIterator[RunningServer]:
    """Serve ``app`` with uvicorn on the pre-bound socket for the duration of the block."""
    journal: list[RecordedRequest] = []
    recorder = _Recorder(app, journal)
    server = uvicorn.Server(
        uvicorn.Config(recorder, log_level="warning", lifespan="on", access_log=False)
    )
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(server.serve, [loopback.sock])
        with anyio.fail_after(10):
            while not server.started:
                await anyio.sleep(0.01)
        try:
            yield RunningServer(url=loopback.url, journal=journal, recorder=recorder)
        finally:
            server.should_exit = True


@dataclass
class TokenLedger:
    """Opaque access tokens issued by fake authorization servers, shared with the RS."""

    issued: dict[str, AccessToken] = field(default_factory=dict)

    def issue(self, *, client_id: str, scopes: list[str], resource: str | None) -> str:
        token = secrets.token_urlsafe(24)
        self.issued[token] = AccessToken(
            token=token, client_id=client_id, scopes=scopes, resource=resource, subject=client_id
        )
        return token


AssertionVerifier = Callable[[str, str], Awaitable[str | None]]


@dataclass
class FakeAuthorizationServer:
    """A minimal RFC 8414 authorization server for the client-credentials grant.

    ``metadata_issuer`` lets a test publish metadata that names a different issuer than the
    URL it is served from. ``assertion_verifier`` enables ``private_key_jwt``: it receives
    ``(assertion, expected_audience)`` and returns the authenticated client ID or ``None``.
    """

    ledger: TokenLedger
    client_id: str
    client_secret: str | None = None
    assertion_verifier: AssertionVerifier | None = None
    issuer: str = ""
    metadata_issuer: str | None = None

    def app(self) -> Starlette:
        async def metadata(_: Request) -> Response:
            methods = []
            if self.client_secret is not None:
                methods.append("client_secret_basic")
            if self.assertion_verifier is not None:
                methods.append("private_key_jwt")
            return JSONResponse(
                {
                    "issuer": self.metadata_issuer or self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["client_credentials"],
                    "token_endpoint_auth_methods_supported": methods,
                    "token_endpoint_auth_signing_alg_values_supported": ["RS256", "ES256"],
                    "scopes_supported": [REQUIRED_SCOPE],
                }
            )

        async def token(request: Request) -> Response:
            form = {
                key: values[-1] for key, values in parse_qs((await request.body()).decode()).items()
            }
            if form.get("grant_type") != "client_credentials":
                return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
            client_id = await self._authenticate(request, form)
            if client_id is None:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            scope = form.get("scope", "")
            access_token = self.ledger.issue(
                client_id=client_id, scopes=scope.split(), resource=form.get("resource")
            )
            return JSONResponse(
                {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "scope": scope or None,
                }
            )

        return Starlette(
            routes=[
                Route("/.well-known/oauth-authorization-server", metadata, methods=["GET"]),
                Route("/.well-known/openid-configuration", metadata, methods=["GET"]),
                Route("/token", token, methods=["POST"]),
            ]
        )

    async def _authenticate(self, request: Request, form: dict[str, str]) -> str | None:
        assertion = form.get("client_assertion")
        if assertion is not None:
            if self.assertion_verifier is None or form.get("client_assertion_type") != (
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            ):
                return None
            return await self.assertion_verifier(assertion, self.issuer)
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic" or self.client_secret is None:
            return None
        try:
            client_id, _, secret = base64.b64decode(encoded).decode().partition(":")
        except ValueError:
            return None
        if secrets.compare_digest(client_id, self.client_id) and secrets.compare_digest(
            secret, self.client_secret
        ):
            return client_id
        return None


class _LedgerTokenVerifier:
    def __init__(self, ledger: TokenLedger) -> None:
        self._ledger = ledger

    async def verify_token(self, token: str) -> AccessToken | None:
        return self._ledger.issued.get(token)


def resource_server_app(
    *, resource_url: str, authorization_server: str, ledger: TokenLedger
) -> Starlette:
    """Build the real SDK Streamable HTTP resource server with one ``whoami`` tool."""
    server: MCPServer[Any] = MCPServer(
        name="integration-rs",
        token_verifier=_LedgerTokenVerifier(ledger),
        # Validated from plain strings so the PRM advertises the issuer byte-for-byte: SDK 2.2
        # compares issuers as simple strings (RFC 8414 section 3.3), so a slash added here
        # would be a real, unrelated mismatch.
        auth=AuthSettings.model_validate(
            {
                "issuer_url": authorization_server,
                "resource_server_url": f"{resource_url}/",
                "required_scopes": [REQUIRED_SCOPE],
                "validate_token_resource": False,
            }
        ),
    )

    def whoami() -> dict[str, object]:
        token = get_access_token()
        assert token is not None
        return {"client_id": token.client_id, "scopes": token.scopes}

    server.tool(name="whoami", description="Return the caller's client ID and scopes.")(whoami)
    return server.streamable_http_app(json_response=True, stateless_http=True)


def assert_never_seen(journals: Iterable[list[RecordedRequest]], *sentinels: str) -> None:
    """Fail if any sentinel appears anywhere in any journaled request."""
    for journal in journals:
        for request in journal:
            raw = request.raw()
            for sentinel in sentinels:
                assert sentinel.encode() not in raw, (
                    f"credential material reached {request.method} {request.path}"
                )
