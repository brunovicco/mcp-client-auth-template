"""Unit tests for the demo entrypoint's observability wiring.

Offline only: no OTLP collector, no network. These also act as a cheap canary against a future
``a2a-otel-kit`` release changing its native MCP SDK 2.x/HTTPX2 transport contract, documented in
``docs/adr/0019-native-mcp-v2-observability.md``.
"""

import httpx2
import pytest
from a2a_otel_kit.adapters.mcp import TracingAsyncTransport
from a2a_otel_kit.entrypoints.observability import Observability

from mcp_client_auth_template.entrypoints.demo_client import build_observability_settings


def test_build_observability_settings_defaults_to_disabled() -> None:
    settings = build_observability_settings()

    assert settings.service_name == "mcp-client-auth-template"
    assert settings.service_version == "0.4.0"
    assert settings.environment == "local"
    assert settings.enabled is False
    assert settings.otlp_endpoint is None


def test_build_observability_settings_reads_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A2A_OTEL_ENABLED", "true")
    monkeypatch.setenv("A2A_OTEL_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    monkeypatch.setenv("A2A_OTEL_LOG_FORMAT", "console")

    settings = build_observability_settings()

    assert settings.enabled is True
    assert settings.otlp_endpoint == "http://localhost:4318/v1/traces"
    assert settings.log_format == "console"


async def test_traced_transport_wiring_is_network_silent_when_disabled() -> None:
    observability = Observability.configure(build_observability_settings())

    transport = TracingAsyncTransport.wrap(httpx2.AsyncHTTPTransport(), observability)
    client = httpx2.AsyncClient(transport=transport)

    assert isinstance(transport, TracingAsyncTransport)
    assert isinstance(client._transport, TracingAsyncTransport)

    await client.aclose()
    observability.shutdown()
