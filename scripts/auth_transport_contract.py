"""Validate the client auth-provider and transport compatibility matrix."""

import argparse
import json
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from mcp_client_auth_template.entrypoints.preflight import validate_production_settings
from mcp_client_auth_template.entrypoints.settings import Settings

Provider = Literal["entra", "generic"]
TransportProfile = Literal["production-https", "loopback-ipv4", "loopback-ipv6"]

_ENTRA_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_ENTRA_CLIENT_ID = "22222222-2222-2222-2222-222222222222"


class AuthTransportContractError(RuntimeError):
    """Raised when a provider/transport profile violates the compatibility contract."""


def _provider_settings(provider: Provider) -> dict[str, object]:
    """Return provider-specific settings without credentials or secrets."""
    if provider == "entra":
        return {
            "entra_tenant_id": _ENTRA_TENANT_ID,
            "entra_client_id": _ENTRA_CLIENT_ID,
        }
    return {"generic_client_metadata_url": "https://client.acme.corp/oauth/client-metadata.json"}


def build_profile_settings(provider: Provider, transport: TransportProfile) -> Settings:
    """Build one supported provider/transport profile without performing network I/O."""
    values: dict[str, object] = {
        "auth_provider": provider,
        "token_storage_path": None,
        **_provider_settings(provider),
    }

    if transport == "production-https":
        values.update(
            server_url="https://mcp.acme.corp/mcp",
            redirect_host="127.0.0.1",
            oauth_allow_insecure_loopback=False,
        )
    elif transport == "loopback-ipv4":
        values.update(
            server_url="http://127.0.0.1:8000/mcp",
            redirect_host="127.0.0.1",
            oauth_allow_insecure_loopback=True,
        )
    elif transport == "loopback-ipv6":
        values.update(
            server_url="http://[::1]:8000/mcp",
            redirect_host="::1",
            oauth_allow_insecure_loopback=True,
        )
    else:
        raise AuthTransportContractError("unsupported transport profile")
    return Settings.model_validate(values)


def validate_profile(provider: Provider, transport: TransportProfile) -> dict[str, object]:
    """Validate one supported profile and return only non-sensitive evidence."""
    settings = build_profile_settings(provider, transport)
    parsed = urlsplit(settings.server_url)

    if transport == "production-https":
        issues = validate_production_settings(settings, "production")
        if issues:
            raise AuthTransportContractError("production compatibility profile failed preflight")
        if parsed.scheme != "https" or settings.oauth_allow_insecure_loopback:
            raise AuthTransportContractError("production transport policy is not fail closed")
        family = "n/a"
    else:
        if parsed.scheme != "http" or parsed.hostname is None:
            raise AuthTransportContractError("loopback profile must use HTTP on a loopback IP")
        address = ip_address(parsed.hostname)
        if not address.is_loopback or not settings.oauth_allow_insecure_loopback:
            raise AuthTransportContractError("loopback HTTP requires explicit local opt-in")
        redirect_address = ip_address(settings.redirect_host)
        if not redirect_address.is_loopback or redirect_address.version != address.version:
            raise AuthTransportContractError("redirect listener must match loopback address family")
        family = f"ipv{address.version}"

    return {
        "status": "ok",
        "provider": provider,
        "transport": transport,
        "server_scheme": parsed.scheme,
        "address_family": family,
    }


def main() -> None:
    """Validate one CI matrix cell."""
    parser = argparse.ArgumentParser(description="Validate client auth/transport compatibility")
    parser.add_argument("--provider", choices=("entra", "generic"), required=True)
    parser.add_argument(
        "--transport",
        choices=("production-https", "loopback-ipv4", "loopback-ipv6"),
        required=True,
    )
    args = parser.parse_args()
    payload = validate_profile(args.provider, args.transport)
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
