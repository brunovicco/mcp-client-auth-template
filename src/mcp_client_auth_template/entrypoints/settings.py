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

    redirect_host: str = "127.0.0.1"
    redirect_port: int = 8765
    redirect_path: str = "/callback"

    token_storage_path: Path | None = Path.home() / ".mcp-client-auth-template" / "tokens.json"

    auth_provider: Literal["entra", "generic"]

    # --- Entra ID mode ---
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: str | None = None

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
