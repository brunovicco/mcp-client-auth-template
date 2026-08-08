"""Process configuration, read from the environment.

Exactly one authorization-server mode is active per run: set
``MCP_CLIENT_AUTH_PROVIDER`` to ``entra`` or ``generic`` and fill in the
matching block below. See ``.env.example`` for a filled-out sample of each.
"""

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the demo OAuth 2.1 MCP client."""

    model_config = SettingsConfigDict(env_prefix="MCP_CLIENT_", env_file=".env", extra="ignore")

    server_url: str
    scope: str = "openid profile"

    # --- OAuth outbound network hardening ---
    oauth_allow_insecure_loopback: bool = False
    oauth_dns_timeout_seconds: float = 2.0
    oauth_http_timeout_seconds: float = 10.0
    oauth_max_response_bytes: int = 1_048_576
    oauth_max_redirects: int = 5
    oauth_max_hosts: int = 16

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

    @property
    def redirect_uri(self) -> str:
        """The loopback URI to register as this client's ``redirect_uri``."""
        return f"http://{self.redirect_host}:{self.redirect_port}{self.redirect_path}"

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
        return self
