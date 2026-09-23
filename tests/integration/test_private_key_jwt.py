"""``private_key_jwt`` machine authentication over the real wire (ADR-0025).

The MCP SDK signs the assertion, and a local authorization server verifies it with the
client's public key. The tests then check that the private key is absent everywhere it could
leak: every journaled request, logs, spans, exceptions, and token storage.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt
import pytest
from a2a_otel_kit.entrypoints.observability import Observability
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.extensions.client_credentials import SignedJWTParameters
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.testing import capture_logs

from mcp_client_auth_template.adapters.client_credentials_auth import ASSERTION_LIFETIME_SECONDS
from mcp_client_auth_template.adapters.token_storage import InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.demo_client import (
    build_oauth_provider,
    build_observability_settings,
)
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

_CLIENT_ID = "integration-pkjwt-client"
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_PRM_PATH = "/.well-known/oauth-protected-resource"


@dataclass
class _AssertionVerifier:
    """RFC 7523 client-assertion checks, applied the way a strict authorization server would."""

    public_key: rsa.RSAPublicKey
    accepted_claims: list[dict[str, Any]] = field(default_factory=list)
    seen_jti: set[str] = field(default_factory=set)

    async def __call__(self, assertion: str, expected_audience: str) -> str | None:
        try:
            claims = jwt.decode(
                assertion,
                self.public_key,
                algorithms=["RS256"],
                audience=expected_audience,
                issuer=_CLIENT_ID,
                options={"require": ["exp", "iat", "jti", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError:
            return None
        if claims["sub"] != _CLIENT_ID or claims["jti"] in self.seen_jti:
            return None
        if claims["exp"] - claims["iat"] > ASSERTION_LIFETIME_SECONDS:
            return None
        self.seen_jti.add(claims["jti"])
        self.accepted_claims.append(claims)
        return _CLIENT_ID


@dataclass
class _Topology:
    trusted: RunningServer
    rogue: RunningServer
    resource: RunningServer
    verifier: _AssertionVerifier
    key_path: Path
    key_pem: str

    def journals(self) -> list[list[RecordedRequest]]:
        return [self.trusted.journal, self.rogue.journal, self.resource.journal]

    def key_fragments(self) -> list[str]:
        """The PEM plus each base64 body line, so a partial leak is caught too."""
        body = [line for line in self.key_pem.splitlines() if not line.startswith("-----")]
        return [self.key_pem, *body]


@asynccontextmanager
async def _topology(*, advertised: str) -> AsyncIterator[_Topology]:
    key = rsa_key()
    verifier = _AssertionVerifier(public_key=key.public_key())
    ledger = TokenLedger()
    trusted_socket, rogue_socket, resource_socket = (
        bind_loopback(),
        bind_loopback(),
        bind_loopback(),
    )
    trusted_as = FakeAuthorizationServer(
        ledger=ledger, client_id=_CLIENT_ID, assertion_verifier=verifier, issuer=trusted_socket.url
    )
    rogue_as = FakeAuthorizationServer(
        ledger=ledger, client_id=_CLIENT_ID, assertion_verifier=verifier, issuer=rogue_socket.url
    )
    authorization_server = {"trusted": trusted_socket.url, "rogue": rogue_socket.url}[advertised]
    with secure_key_dir() as key_dir:
        key_path = write_key(key_dir / "client.pem", pem(key))
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
                verifier=verifier,
                key_path=key_path,
                key_pem=key_path.read_text(),
            )


def _settings(topology: _Topology) -> Settings:
    return Settings(
        auth_provider="generic",
        auth_mode="client_credentials",
        client_auth_method="private_key_jwt",
        server_url=topology.resource.url,
        scope=REQUIRED_SCOPE,
        oauth_allow_insecure_loopback=True,
        client_credentials_client_id=_CLIENT_ID,
        client_credentials_private_key_path=topology.key_path,
        client_credentials_issuer=topology.trusted.url,
    )


def _recording_observability() -> tuple[Observability, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability = Observability(
        settings=build_observability_settings(),
        tracer=provider.get_tracer("pkjwt-integration"),
        lifecycle=None,
    )
    return observability, exporter


def _span_text(exporter: InMemorySpanExporter) -> str:
    parts: list[str] = []
    for span in exporter.get_finished_spans():
        parts.append(span.name)
        parts.extend(f"{key}={value}" for key, value in (span.attributes or {}).items())
        for event in span.events:
            parts.extend(f"{key}={value}" for key, value in (event.attributes or {}).items())
        parts.append(str(span.status.description))
    return "\n".join(parts)


@pytest.fixture
def signing_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Observe every assertion the SDK signs without changing how it signs."""
    audiences: list[str] = []
    create = SignedJWTParameters.create_assertion_provider

    def observed(self: SignedJWTParameters) -> Callable[[str], Awaitable[str]]:
        sign = create(self)

        async def provider(audience: str) -> str:
            audiences.append(audience)
            return await sign(audience)

        return provider

    monkeypatch.setattr(SignedJWTParameters, "create_assertion_provider", observed)
    return audiences


async def test_sdk_signed_assertion_authenticates_at_the_bound_issuer_only(
    signing_calls: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with _topology(advertised="trusted") as topology:
        settings = _settings(topology)
        storage = InMemoryTokenStorage()
        observability, exporter = _recording_observability()
        with capture_logs() as structured_logs:
            provider = await build_oauth_provider(settings, storage=storage)
            async with production_client(
                settings, oauth_provider=provider, observability=observability
            ) as client:
                result = await client.call_tool("whoami")

        assert result.structured_content == {"client_id": _CLIENT_ID, "scopes": [REQUIRED_SCOPE]}

        (token_request,) = topology.trusted.requests_to("/token")
        form = token_request.form()
        assert form["grant_type"] == "client_credentials"
        assert form["client_assertion_type"] == _ASSERTION_TYPE
        assert "client_secret" not in form
        assert token_request.header("authorization") is None

        (claims,) = topology.verifier.accepted_claims
        assert claims["aud"] == topology.trusted.url
        assert claims["iss"] == claims["sub"] == _CLIENT_ID
        assert claims["exp"] - claims["iat"] == ASSERTION_LIFETIME_SECONDS
        assert signing_calls == [topology.trusted.url]
        assert topology.rogue.journal == []

        fragments = topology.key_fragments()
        assert_never_seen(topology.journals(), *fragments)
        log_text = "\n".join(
            [repr(entry) for entry in structured_logs]
            + [f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records]
        )
        span_text = _span_text(exporter)
        stored = repr(await storage.get_tokens()) + repr(await storage.get_client_info())
        for fragment in fragments:
            assert fragment not in log_text
            assert fragment not in span_text
            assert fragment not in stored
        assert await storage.get_client_info() is None


async def test_no_assertion_is_signed_for_an_authorization_server_the_prm_substitutes(
    signing_calls: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with _topology(advertised="rogue") as topology:
        settings = _settings(topology)
        provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())
        observability, exporter = _recording_observability()

        with pytest.RaisesGroup(
            pytest.RaisesExc(OAuthFlowError, match="metadata issuer mismatch"),
            allow_unwrapped=True,
            flatten_subgroups=True,
        ) as raised:
            async with production_client(
                settings, oauth_provider=provider, observability=observability
            ):
                pytest.fail("the client connected through an unexpected authorization server")

        assert signing_calls == []
        assert topology.verifier.accepted_claims == []
        for request in topology.rogue.journal:
            assert request.method == "GET"
            assert request.header("authorization") is None
            assert "client_assertion" not in request.form()
        assert topology.trusted.journal == []

        fragments = topology.key_fragments()
        assert_never_seen(topology.journals(), *fragments, _CLIENT_ID)
        rendered = f"{raised.value!s} {raised.value!r} {_span_text(exporter)} " + " ".join(
            f"{record.getMessage()} {record.exc_text or ''}" for record in caplog.records
        )
        for fragment in fragments:
            assert fragment not in rendered


async def test_an_assertion_for_another_client_key_is_rejected_without_leaking_the_key() -> None:
    async with _topology(advertised="trusted") as topology:
        topology.verifier.public_key = rsa_key().public_key()
        settings = _settings(topology)
        provider = await build_oauth_provider(settings, storage=InMemoryTokenStorage())

        with pytest.RaisesGroup(
            pytest.RaisesExc(OAuthFlowError),
            allow_unwrapped=True,
            flatten_subgroups=True,
        ) as raised:
            async with production_client(settings, oauth_provider=provider):
                pytest.fail("an assertion signed with an unregistered key was accepted")

        assert len(topology.trusted.requests_to("/token")) == 1
        assert topology.verifier.accepted_claims == []
        for fragment in topology.key_fragments():
            assert fragment not in f"{raised.value!s} {raised.value!r}"
