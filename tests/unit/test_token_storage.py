"""Unit tests for :class:`InMemoryTokenStorage` and :class:`FileTokenStorage`."""

import errno
import json
import os
import stat
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from mcp_client_auth_template.adapters.token_storage import (
    FileTokenStorage,
    InMemoryTokenStorage,
    TokenStorageCorruptionError,
    TokenStorageSecurityError,
)

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
    storage = FileTokenStorage(tmp_path / "private" / "tokens.json")

    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None


async def test_file_storage_round_trips_tokens_and_client_info(tmp_path: Path) -> None:
    storage = FileTokenStorage(tmp_path / "private" / "tokens.json")

    await storage.set_tokens(_TOKENS)
    await storage.set_client_info(_CLIENT_INFO)

    assert await storage.get_tokens() == _TOKENS
    assert await storage.get_client_info() == _CLIENT_INFO


async def test_file_storage_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    await FileTokenStorage(path).set_tokens(_TOKENS)

    reopened = FileTokenStorage(path)

    assert await reopened.get_tokens() == _TOKENS


async def test_file_storage_restricts_file_and_directory_permissions(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    await FileTokenStorage(path).set_tokens(_TOKENS)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_file_storage_creates_its_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "private" / "tokens.json"

    await FileTokenStorage(path).set_tokens(_TOKENS)

    assert path.exists()


def test_storage_does_not_depend_on_macos_o_nofollow_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_flag = 1 << 29
    original_open = os.open

    def rejecting_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & synthetic_flag:
            raise OSError(errno.EINVAL, "synthetic O_NOFOLLOW_ANY incompatibility")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "O_NOFOLLOW_ANY", synthetic_flag, raising=False)
    monkeypatch.setattr(os, "open", rejecting_open)

    FileTokenStorage(tmp_path / "private" / "tokens.json")


def test_existing_permissive_parent_is_rejected(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)  # noqa: S103 - intentionally insecure fixture for rejection test

    with pytest.raises(TokenStorageSecurityError, match="0700"):
        FileTokenStorage(parent / "tokens.json")


async def test_existing_permissive_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    storage = FileTokenStorage(path)
    path.write_text(json.dumps({"tokens": _TOKENS.model_dump(mode="json")}), encoding="utf-8")
    os.chmod(path, 0o644)

    with pytest.raises(TokenStorageSecurityError, match="0600"):
        await storage.get_tokens()


async def test_symlink_token_file_is_rejected_without_following_target(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    storage = FileTokenStorage(path)
    target = tmp_path / "target.json"
    target.write_text('{"sentinel":"unchanged"}', encoding="utf-8")
    path.symlink_to(target)

    with pytest.raises(TokenStorageSecurityError, match="symbolic link"):
        await storage.set_tokens(_TOKENS)

    assert target.read_text(encoding="utf-8") == '{"sentinel":"unchanged"}'


def test_symlinked_storage_directory_is_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(TokenStorageSecurityError, match="symbolic-link components"):
        FileTokenStorage(alias / "tokens.json")


async def test_hard_linked_token_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    storage = FileTokenStorage(path)
    await storage.set_tokens(_TOKENS)
    os.link(path, tmp_path / "second-link.json")

    with pytest.raises(TokenStorageSecurityError, match="hard links"):
        await storage.get_tokens()


async def test_existing_empty_storage_file_is_corruption(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    storage = FileTokenStorage(path)
    path.touch(mode=0o600)
    os.chmod(path, 0o600)

    with pytest.raises(TokenStorageCorruptionError, match="empty"):
        await storage.get_tokens()


async def test_corrupt_or_oversized_storage_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "private" / "tokens.json"
    storage = FileTokenStorage(path)
    path.write_text("not-json", encoding="utf-8")
    os.chmod(path, 0o600)

    with pytest.raises(TokenStorageCorruptionError, match="UTF-8 JSON"):
        await storage.get_tokens()

    path.write_bytes(b"{" + b"x" * (1024 * 1024))
    os.chmod(path, 0o600)
    with pytest.raises(TokenStorageCorruptionError, match="1 MiB"):
        await storage.get_tokens()


async def test_failed_atomic_replace_keeps_previous_file_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private" / "tokens.json"
    storage = FileTokenStorage(path)
    await storage.set_tokens(_TOKENS)
    original = path.read_bytes()

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        await storage.set_client_info(_CLIENT_INFO)

    assert path.read_bytes() == original
    assert list(path.parent.glob(".*.tmp")) == []
