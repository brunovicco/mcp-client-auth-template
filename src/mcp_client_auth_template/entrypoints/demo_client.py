"""End-to-end demo: authenticate and call the companion server's example tools.

Run against the server template (``mcp-server-auth-template``) with:

    uv run python -m mcp_client_auth_template.entrypoints.demo_client

Interactive mode opens a browser for the authorization code flow on its first run; if
``MCP_CLIENT_TOKEN_STORAGE_PATH`` resolves to a real path (the default,
``~/.mcp-client-auth-template/tokens.json``), later runs reuse the stored
token and refresh it silently instead of prompting again.

Client-credentials mode is non-interactive and keeps acquired tokens in memory.
"""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import cast

import anyio
import httpx
import httpx2
import structlog
from a2a_otel_kit.adapters.mcp import TracingAsyncTransport
from a2a_otel_kit.application.settings import ObservabilitySettings
from a2a_otel_kit.entrypoints.observability import Observability
from mcp.client import Client, advertise
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult
from mcp.types import CallToolResult

from mcp_client_auth_template.adapters.browser_redirect import open_system_browser
from mcp_client_auth_template.adapters.client_credentials_auth import (
    OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID,
    build_client_credentials_oauth_provider,
)
from mcp_client_auth_template.adapters.entra_client_auth import build_entra_oauth_provider
from mcp_client_auth_template.adapters.generic_oidc_client_auth import build_generic_oauth_provider
from mcp_client_auth_template.adapters.loopback_callback_server import LoopbackCallbackServer
from mcp_client_auth_template.adapters.oauth_discovery_security import (
    OAuthDiscoverySecurityPolicy,
    PinnedDnsAsyncTransport,
)
from mcp_client_auth_template.adapters.token_storage import FileTokenStorage, InMemoryTokenStorage
from mcp_client_auth_template.entrypoints.cli_failures import (
    ClientExitCode,
    ToolCallFailedError,
    classify_failure,
    emit_failure,
)
from mcp_client_auth_template.entrypoints.preflight import load_validated_settings
from mcp_client_auth_template.entrypoints.settings import Settings

logger = structlog.get_logger(__name__)

_SERVICE_NAME = "mcp-client-auth-template"
_SERVICE_VERSION = "0.1.0"
_ENVIRONMENT = "local"


def build_token_storage(settings: Settings) -> TokenStorage:
    """Return a persistent :class:`FileTokenStorage` or, with no path configured, in-memory."""
    if settings.token_storage_path is None:
        return InMemoryTokenStorage()
    return FileTokenStorage(settings.token_storage_path)


def build_oauth_network_policy(settings: Settings) -> OAuthDiscoverySecurityPolicy:
    """Build the fail-closed outbound policy shared by OAuth HTTP and browser redirects."""
    return OAuthDiscoverySecurityPolicy(
        resource_server_url=settings.server_url,
        allow_insecure_loopback=settings.oauth_allow_insecure_loopback,
        dns_timeout_seconds=settings.oauth_dns_timeout_seconds,
        http_timeout_seconds=settings.oauth_http_timeout_seconds,
        max_oauth_response_bytes=settings.oauth_max_response_bytes,
    )


def build_secure_http_transport(
    settings: Settings,
    *,
    policy: OAuthDiscoverySecurityPolicy,
    observability: Observability,
) -> httpx2.AsyncBaseTransport:
    """Wrap DNS-pinned egress with tracing and no implicit transport retries."""

    def transport_factory() -> httpx2.AsyncBaseTransport:
        return httpx2.AsyncHTTPTransport(retries=0)

    secure_transport = PinnedDnsAsyncTransport(
        policy=policy,
        transport_factory=transport_factory,
        max_hosts=settings.oauth_max_hosts,
    )
    traced = TracingAsyncTransport.wrap(
        cast(httpx.AsyncBaseTransport, secure_transport), observability
    )
    return cast(httpx2.AsyncBaseTransport, traced)


def build_observability_settings() -> ObservabilitySettings:
    """Build this demo's observability identity; export/logging config comes from the environment.

    Service identity is fixed for this single-entrypoint demo. ``ObservabilitySettings`` reads its
    remaining fields (``enabled``, ``otlp_endpoint``, ``otlp_timeout_seconds``, ``log_level``,
    ``log_format``) from ``A2A_OTEL_*``-prefixed environment variables itself; leaving them unset
    keeps ``enabled=False``, the network-silent default documented in ``docs/OBSERVABILITY.md``.
    """
    return ObservabilitySettings(
        service_name=_SERVICE_NAME, service_version=_SERVICE_VERSION, environment=_ENVIRONMENT
    )


async def build_oauth_provider(
    settings: Settings,
    *,
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]] | None = None,
    callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]] | None = None,
) -> OAuthClientProvider:
    """Return the adapter matching ``settings.auth_mode`` and ``auth_provider``.

    ``Settings._finalize`` already enforces that ``entra_tenant_id``/``entra_client_id`` are set
    when ``auth_provider="entra"``; the ``cast`` calls below are type narrowing for mypy, not a
    second validation pass.
    """
    if settings.auth_mode == "client_credentials":
        client_id = cast(str, settings.client_credentials_client_id)
        secret = settings.client_credentials_secret
        if secret is None:  # pragma: no cover - Settings validates this invariant
            raise RuntimeError("client credentials settings were not validated")
        return build_client_credentials_oauth_provider(
            server_url=settings.server_url,
            storage=storage,
            client_id=client_id,
            client_secret=secret.get_secret_value(),
            scope=settings.scope,
        )

    if redirect_handler is None or callback_handler is None:
        raise ValueError("interactive auth requires redirect and callback handlers")

    if settings.auth_provider == "entra":
        return await build_entra_oauth_provider(
            server_url=settings.server_url,
            tenant_id=cast(str, settings.entra_tenant_id),
            client_id=cast(str, settings.entra_client_id),
            redirect_uri=settings.redirect_uri,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            scope=settings.scope,
        )

    return await build_generic_oauth_provider(
        server_url=settings.server_url,
        redirect_uri=settings.redirect_uri,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        client_metadata_url=settings.generic_client_metadata_url,
        scope=settings.scope,
    )


def build_http_timeout(settings: Settings) -> httpx2.Timeout:
    """Build distinct connection, read, write and pool budgets for the shared HTTP stack."""
    return httpx2.Timeout(
        connect=settings.http_connect_timeout_seconds,
        read=settings.http_read_timeout_seconds,
        write=settings.http_write_timeout_seconds,
        pool=settings.http_pool_timeout_seconds,
    )


async def call_tool_with_budget(
    client: Client,
    tool_name: str,
    *,
    timeout_seconds: float,
) -> CallToolResult:
    """Call one MCP tool once and cancel the in-flight request when its budget expires."""
    try:
        with anyio.fail_after(timeout_seconds):
            result = await client.call_tool(tool_name)
        if result.is_error:
            logger.warning("mcp_tool_failed", tool_name=tool_name)
            raise ToolCallFailedError(tool_name)
        return result
    except TimeoutError:
        logger.warning(
            "mcp_tool_timeout",
            tool_name=tool_name,
            timeout_seconds=timeout_seconds,
        )
        raise


async def close_with_budget(exit_stack: AsyncExitStack, *, timeout_seconds: float) -> bool:
    """Close async client resources under a shielded deadline; return whether it expired."""
    with anyio.move_on_after(timeout_seconds, shield=True) as scope:
        await exit_stack.aclose()
    return scope.cancelled_caught


def build_mcp_client(settings: Settings, *, http_client: httpx2.AsyncClient) -> Client:
    """Return the SDK v2 high-level client with automatic protocol negotiation.

    ``mode="auto"`` probes ``server/discover`` first. A 2026-07-28 server therefore
    remains on the modern sessionless protocol path, while older servers are still
    supported through the SDK-managed legacy initialization fallback.
    """
    transport = streamable_http_client(
        f"{settings.server_url.rstrip('/')}/mcp",
        http_client=http_client,
    )
    extensions = None
    if settings.auth_mode == "client_credentials":
        extensions = [advertise(OAUTH_CLIENT_CREDENTIALS_EXTENSION_ID)]
    return Client(transport, mode="auto", extensions=extensions)


async def run_demo() -> None:
    """Authenticate against the configured provider and call ``whoami`` and ``health``."""
    settings = load_validated_settings()
    observability = Observability.configure(build_observability_settings())
    exit_stack = AsyncExitStack()
    try:
        storage = build_token_storage(settings)
        network_policy = build_oauth_network_policy(settings)
        if settings.auth_mode == "interactive":
            loopback = LoopbackCallbackServer(
                host=settings.redirect_host,
                port=settings.redirect_port,
                path=settings.redirect_path,
                timeout_seconds=settings.oauth_callback_timeout_seconds,
                max_requests=settings.oauth_callback_max_requests,
            )
            oauth_provider = await build_oauth_provider(
                settings,
                storage=storage,
                redirect_handler=network_policy.wrap_browser_redirect(
                    loopback.wrap_redirect_handler(open_system_browser)
                ),
                callback_handler=loopback.wait_for_callback,
            )
        else:
            oauth_provider = await build_oauth_provider(settings, storage=storage)

        # Tracing remains the outer transport so spans keep the logical hostname. The security
        # transport underneath resolves and pins the actual connect IP without exposing that
        # implementation detail as the application-level request URL. Child transports use
        # retries=0 so only the caller can make an idempotency-aware retry decision.
        transport = build_secure_http_transport(
            settings, policy=network_policy, observability=observability
        )
        http_client = await exit_stack.enter_async_context(
            httpx2.AsyncClient(
                auth=oauth_provider,
                follow_redirects=True,
                max_redirects=settings.oauth_max_redirects,
                timeout=build_http_timeout(settings),
                transport=transport,
            )
        )
        client = await exit_stack.enter_async_context(
            build_mcp_client(settings, http_client=http_client)
        )
        logger.info("mcp_connected", protocol_version=client.protocol_version)

        await call_tool_with_budget(
            client, "whoami", timeout_seconds=settings.tool_call_timeout_seconds
        )
        logger.info("mcp_tool_completed", tool_name="whoami")

        await call_tool_with_budget(
            client, "health", timeout_seconds=settings.tool_call_timeout_seconds
        )
        logger.info("mcp_tool_completed", tool_name="health")
    finally:
        try:
            shutdown_timed_out = await close_with_budget(
                exit_stack, timeout_seconds=settings.shutdown_timeout_seconds
            )
            if shutdown_timed_out:
                logger.warning(
                    "mcp_shutdown_timeout",
                    timeout_seconds=settings.shutdown_timeout_seconds,
                )
        finally:
            observability.shutdown()


def run_cli() -> ClientExitCode:
    """Run the demo and translate expected failures into stable process exit codes."""
    try:
        anyio.run(run_demo)
    except KeyboardInterrupt:
        logger.warning("mcp_client_interrupted")
        return ClientExitCode.INTERRUPTED
    except Exception as exc:
        failure = classify_failure(exc)
        emit_failure(failure)
        return failure.exit_code
    return ClientExitCode.SUCCESS


def main() -> None:
    """Synchronous entrypoint for ``uv run python -m ...``."""
    raise SystemExit(int(run_cli()))


if __name__ == "__main__":
    main()
