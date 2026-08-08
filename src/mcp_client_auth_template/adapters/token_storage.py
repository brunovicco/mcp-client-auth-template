"""``TokenStorage`` implementations (``mcp.client.auth.TokenStorage``).

The SDK's ``OAuthClientProvider`` reads and writes through this Protocol; it
never persists tokens or client registration itself. Two implementations
cover the two things a template needs to demonstrate:

- :class:`InMemoryTokenStorage` - nothing survives past the process, which is
  exactly right for tests and for the CIMD/DCR case where re-registering on
  every run is cheap and harmless.
- :class:`FileTokenStorage` - persists across runs in a single JSON file with
  owner-only permissions, so a demo (or a real CLI built from this template)
  does not force the user through the browser consent screen every time.

Neither implementation is appropriate for a multi-user server process - both
are scoped to one local user running one client.
"""

import json
import os
import stat
from pathlib import Path

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class InMemoryTokenStorage:
    """Tokens and client info held only in process memory."""

    def __init__(self) -> None:
        """Start with no stored tokens or client registration."""
        self._tokens: OAuthToken | None = None
        self._client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """Return the stored tokens, if any."""
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Replace the stored tokens."""
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return the stored client registration, if any."""
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Replace the stored client registration."""
        self._client_info = client_info


class FileTokenStorage:
    """Tokens and client info persisted as owner-only-readable JSON on disk.

    A real deployment built from this template should prefer an OS keyring
    or a secrets manager; this exists so the demo entrypoint (and anyone
    copying it as a starting point) has a persistence option with no extra
    dependency, not as a recommendation for production secret storage.
    """

    def __init__(self, path: Path) -> None:
        """Store state in the JSON file at ``path``, creating its parent directory."""
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def get_tokens(self) -> OAuthToken | None:
        """Return the stored tokens, if the file has a ``tokens`` entry."""
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Persist ``tokens``, keeping any stored client registration."""
        self._write_field("tokens", tokens.model_dump(mode="json"))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return the stored client registration, if the file has one."""
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Persist ``client_info``, keeping any stored tokens."""
        self._write_field("client_info", client_info.model_dump(mode="json"))

    def _read(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        return dict(json.loads(self._path.read_text(encoding="utf-8")))

    def _write_field(self, field: str, value: object) -> None:
        data = self._read()
        data[field] = value
        self._path.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
