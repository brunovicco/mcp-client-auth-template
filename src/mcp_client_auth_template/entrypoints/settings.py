"""Process configuration, read from the environment.

Exactly one authorization-server mode is active per run: set
``MCP_CLIENT_AUTH_PROVIDER`` to ``entra`` or ``generic`` and fill in the
matching block below. See ``.env.example`` for a filled-out sample of each.
"""

from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the demo OAuth 2.1 MCP client."""

    model_config = SettingsConfigDict(env_prefix="MCP_CLIENT_", env_file=".env", extra="ignore")

    server_url: str
    scope: str = "openid profile"
    auth_mode: Literal["interactive", "client_credentials"] = "interactive"

    # --- OAuth outbound network hardening ---
    oauth_allow_insecure_loopback: bool = False
    oauth_dns_timeout_seconds: float = 2.0
    oauth_http_timeout_seconds: float = 10.0
    oauth_max_response_bytes: int = 1_048_576
    oauth_max_redirects: int = 5
    oauth_max_hosts: int = 16
    oauth_callback_timeout_seconds: float = 300.0
    oauth_callback_max_requests: int = 32

    # --- MCP/HTTP operational budgets ---
    http_connect_timeout_seconds: float = 10.0
    http_read_timeout_seconds: float = 300.0
    http_write_timeout_seconds: float = 30.0
    http_pool_timeout_seconds: float = 10.0
    tool_call_timeout_seconds: float = 60.0
    shutdown_timeout_seconds: float = 10.0

    redirect_host: str = "127.0.0.1"
    redirect_port: int = 8765
    redirect_path: str = "/callback"

    token_storage_path: Path | None = Path.home() / ".mcp-client-auth-template" / "tokens.json"

    auth_provider: Literal["entra", "generic"]

    # --- Entra ID mode ---
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None

    # --- Generic OIDC mode ---
    generic_client_metadata_url: str | None = None

    # --- OAuth Client Credentials extension (generic OIDC only) ---
    client_credentials_client_id: str | None = None
    client_credentials_secret: SecretStr | None = None
    # Issuer identifier of the authorization server that provisioned the credential. The SDK
    # sends the credential only to metadata discovered for exactly this issuer (ADR-0024).
    client_credentials_issuer: str | None = None
    # How the machine client authenticates at the token endpoint. ``private_key_jwt`` signs a
    # short-lived client assertion with a key read from a secret-mount file (ADR-0025).
    client_auth_method: Literal["client_secret_basic", "private_key_jwt"] = "client_secret_basic"
    client_credentials_private_key_path: Path | None = None

    @property
    def redirect_uri(self) -> str:
        """The loopback URI to register as this client's ``redirect_uri``."""
        host = f"[{self.redirect_host}]" if ":" in self.redirect_host else self.redirect_host
        return f"http://{host}:{self.redirect_port}{self.redirect_path}"

    def _validate_server_url(self) -> None:
        parsed = urlsplit(self.server_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("server_url must be an absolute http(s) URL")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("server_url must not contain credentials, query, or fragment")
        if parsed.scheme == "http":
            self._require_loopback_http(parsed.hostname, "server_url")

    def _require_loopback_http(self, host: str, field_name: str) -> None:
        if not self.oauth_allow_insecure_loopback:
            raise ValueError(
                f"HTTP {field_name} requires oauth_allow_insecure_loopback=true "
                "for local development"
            )
        if host != "localhost":
            try:
                address = ip_address(host)
            except ValueError as exc:
                raise ValueError(f"HTTP {field_name} is allowed only for loopback hosts") from exc
            if not address.is_loopback:
                raise ValueError(f"HTTP {field_name} is allowed only for loopback hosts")

    def _validate_client_credentials_issuer(self, issuer: str) -> None:
        """Structural, fail-closed checks only; issuer matching itself belongs to the SDK."""
        self._validate_credential_identifier(issuer, "client_credentials_issuer")
        parsed = urlsplit(issuer)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("client_credentials_issuer must be an absolute http(s) URL")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in issuer
            or "#" in issuer
        ):
            raise ValueError(
                "client_credentials_issuer must not contain credentials, query, or fragment"
            )
        if parsed.scheme == "http":
            self._require_loopback_http(parsed.hostname, "client_credentials_issuer")

    def _validate_redirect_listener(self) -> None:
        try:
            address = ip_address(self.redirect_host)
        except ValueError as exc:
            raise ValueError("redirect_host must be an IP-literal loopback address") from exc
        if not address.is_loopback:
            raise ValueError("redirect_host must be an IP-literal loopback address")
        if not 1 <= self.redirect_port <= 65535:
            raise ValueError("redirect_port must be between 1 and 65535")
        if (
            not self.redirect_path.startswith("/")
            or "?" in self.redirect_path
            or "#" in self.redirect_path
            or "\\" in self.redirect_path
        ):
            raise ValueError("redirect_path must be an absolute path without query or fragment")

    def _validate_scope(self) -> None:
        if not self.scope.strip() or self.scope.strip() != self.scope:
            raise ValueError("scope must be a non-empty trimmed string")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.scope):
            raise ValueError("scope must not contain control characters")

    @staticmethod
    def _validate_credential_identifier(value: str, field_name: str) -> None:
        if not value or value.strip() != value:
            raise ValueError(f"{field_name} must be a non-empty trimmed string")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError(f"{field_name} must not contain control characters")

    def _validate_generic_metadata_url(self) -> None:
        if self.generic_client_metadata_url is None:
            return
        parsed = urlsplit(self.generic_client_metadata_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "generic_client_metadata_url must be an absolute HTTPS URL without credentials, "
                "query, or fragment"
            )

    @model_validator(mode="after")
    def _finalize(self) -> "Settings":
        if self.token_storage_path is not None:
            self.token_storage_path = self.token_storage_path.expanduser()

        if self.oauth_dns_timeout_seconds <= 0:
            raise ValueError("oauth_dns_timeout_seconds must be positive")
        if self.oauth_http_timeout_seconds <= 0:
            raise ValueError("oauth_http_timeout_seconds must be positive")
        if self.oauth_max_response_bytes <= 0:
            raise ValueError("oauth_max_response_bytes must be positive")
        if self.oauth_max_redirects < 0:
            raise ValueError("oauth_max_redirects must be zero or positive")
        if self.oauth_max_hosts <= 0:
            raise ValueError("oauth_max_hosts must be positive")
        if self.oauth_callback_timeout_seconds <= 0:
            raise ValueError("oauth_callback_timeout_seconds must be positive")
        if self.oauth_callback_max_requests <= 0:
            raise ValueError("oauth_callback_max_requests must be positive")

        for field_name, value in (
            ("http_connect_timeout_seconds", self.http_connect_timeout_seconds),
            ("http_read_timeout_seconds", self.http_read_timeout_seconds),
            ("http_write_timeout_seconds", self.http_write_timeout_seconds),
            ("http_pool_timeout_seconds", self.http_pool_timeout_seconds),
            ("tool_call_timeout_seconds", self.tool_call_timeout_seconds),
            ("shutdown_timeout_seconds", self.shutdown_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")

        self._validate_server_url()
        if self.auth_mode == "interactive":
            self._validate_redirect_listener()
        self._validate_scope()
        self._validate_generic_metadata_url()

        if self.auth_mode == "interactive":
            machine_only = {
                "client_credentials_issuer": self.client_credentials_issuer,
                "client_credentials_private_key_path": self.client_credentials_private_key_path,
            }
            for machine_field, configured in machine_only.items():
                if configured is not None:
                    raise ValueError(
                        f"{machine_field} is used only with auth_mode=client_credentials"
                    )
            if self.client_auth_method != "client_secret_basic":
                raise ValueError(
                    "client_auth_method is used only with auth_mode=client_credentials"
                )

        if self.auth_mode == "client_credentials":
            if self.auth_provider != "generic":
                raise ValueError(
                    "auth_mode=client_credentials currently supports auth_provider=generic only"
                )
            if self.generic_client_metadata_url is not None:
                raise ValueError(
                    "generic_client_metadata_url is not used with auth_mode=client_credentials"
                )
            credential_field, unused_field = (
                ("client_credentials_private_key_path", "client_credentials_secret")
                if self.client_auth_method == "private_key_jwt"
                else ("client_credentials_secret", "client_credentials_private_key_path")
            )
            missing = [
                name
                for name, value in (
                    ("client_credentials_client_id", self.client_credentials_client_id),
                    (credential_field, getattr(self, credential_field)),
                    ("client_credentials_issuer", self.client_credentials_issuer),
                )
                if value is None or (isinstance(value, str) and not value)
            ]
            if missing:
                raise ValueError(f"auth_mode=client_credentials requires: {', '.join(missing)}")
            if getattr(self, unused_field) is not None:
                raise ValueError(
                    f"{unused_field} is not used with client_auth_method={self.client_auth_method}"
                )
            self._validate_credential_identifier(
                self.client_credentials_client_id or "", "client_credentials_client_id"
            )
            secret = self.client_credentials_secret
            if self.client_auth_method == "client_secret_basic" and (
                secret is None or not secret.get_secret_value()
            ):
                raise ValueError("client_credentials_secret must not be empty")
            key_path = self.client_credentials_private_key_path
            if key_path is not None and not key_path.is_absolute():
                raise ValueError("client_credentials_private_key_path must be an absolute path")
            self._validate_client_credentials_issuer(self.client_credentials_issuer or "")
            # Access tokens from this flow are short-lived and can be reacquired with the
            # configured credential. Do not persist either tokens or fixed client credentials.
            self.token_storage_path = None

        if self.auth_provider == "entra":
            missing = [
                name
                for name, value in (
                    ("entra_tenant_id", self.entra_tenant_id),
                    ("entra_client_id", self.entra_client_id),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"auth_provider=entra requires: {', '.join(missing)}")
            try:
                self.entra_tenant_id = str(UUID(self.entra_tenant_id or ""))
            except ValueError as exc:
                raise ValueError("entra_tenant_id must be a tenant-specific UUID") from exc
            try:
                self.entra_client_id = str(UUID(self.entra_client_id or ""))
            except ValueError as exc:
                raise ValueError("entra_client_id must be an application UUID") from exc
        return self
