"""Unit tests for :class:`InMemoryTokenStorage` and :class:`FileTokenStorage`."""

import stat
from pathlib import Path

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from mcp_client_auth_template.adapters.token_storage import FileTokenStorage, InMemoryTokenStorage

_TOKENS = OAuthToken(
    access_token="opaque-access-token", token_type="Bearer", refresh_token="opaque-refresh-token"
)
_CLIENT_INFO = OAuthClientInformationFull(client_id="client-123")


async def test_in_memory_storage_starts_empty() -> None:
    storage = InMemoryTokenStorage()

    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None


async def test_in_memory_storage_round_trips_tokens_and_client_info() -> None:
    storage = InMemoryTokenStorage()

    await storage.set_tokens(_TOKENS)
    await storage.set_client_info(_CLIENT_INFO)

    assert await storage.get_tokens() == _TOKENS
    assert await storage.get_client_info() == _CLIENT_INFO


async def test_file_storage_starts_empty(tmp_path: Path) -> None:
    storage = FileTokenStorage(tmp_path / "tokens.json")

    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None


async def test_file_storage_round_trips_tokens_and_client_info(tmp_path: Path) -> None:
    storage = FileTokenStorage(tmp_path / "tokens.json")

    await storage.set_tokens(_TOKENS)
    await storage.set_client_info(_CLIENT_INFO)

    assert await storage.get_tokens() == _TOKENS
    assert await storage.get_client_info() == _CLIENT_INFO


async def test_file_storage_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    await FileTokenStorage(path).set_tokens(_TOKENS)

    reopened = FileTokenStorage(path)

    assert await reopened.get_tokens() == _TOKENS


async def test_file_storage_restricts_permissions_to_the_owner(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    await FileTokenStorage(path).set_tokens(_TOKENS)

    mode = stat.S_IMODE(path.stat().st_mode)

    assert mode == stat.S_IRUSR | stat.S_IWUSR


async def test_file_storage_creates_its_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "tokens.json"

    await FileTokenStorage(path).set_tokens(_TOKENS)

    assert path.exists()
