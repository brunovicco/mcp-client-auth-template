"""``TokenStorage`` implementations (``mcp.client.auth.TokenStorage``).

The SDK's ``OAuthClientProvider`` reads and writes through this Protocol; it
never persists tokens or client registration itself. Two implementations
cover the two things a template needs to demonstrate:

- :class:`InMemoryTokenStorage` - nothing survives past the process, which is
  exactly right for tests and for the CIMD/DCR case where re-registering on
  every run is cheap and harmless.
- :class:`FileTokenStorage` - persists across runs in a single JSON file in a
  private POSIX directory. Reads reject links or unsafe metadata and writes
  replace the file atomically only after the new contents are durable.

Neither implementation is appropriate for a multi-user server process - both
are scoped to one local user running one client.
"""

import errno
import json
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

_DIRECTORY_MODE = stat.S_IRWXU
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
_MAX_STORAGE_BYTES = 1024 * 1024
_TEMP_ATTEMPTS = 32


class TokenStorageSecurityError(RuntimeError):
    """Raised when local token storage cannot satisfy its filesystem security contract."""


class TokenStorageCorruptionError(RuntimeError):
    """Raised when the token-store file is not a valid bounded JSON object."""


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
    """Persist OAuth state in a private, atomic POSIX file.

    The plaintext JSON format is deliberately simple, but filesystem handling
    is fail-closed: the containing directory must be owned by the current user
    and mode ``0700``; the file must be a single-link regular file owned by the
    same user and mode ``0600``; symlinked path components are rejected; reads
    are bounded; and writes use ``fsync`` + same-directory ``os.replace``.

    Windows does not expose the POSIX ownership/``dir_fd`` guarantees this
    adapter relies on. Use :class:`InMemoryTokenStorage` there, or replace this
    adapter with an OS keyring/secret-manager implementation.
    """

    def __init__(self, path: Path) -> None:
        """Prepare a private storage directory and remember its normalized file path."""
        if os.name != "posix":  # pragma: no cover - project CI and reference runtime are POSIX
            raise TokenStorageSecurityError(
                "FileTokenStorage requires POSIX filesystem security primitives; "
                "use InMemoryTokenStorage or an OS keyring on this platform"
            )

        candidate = Path(os.path.abspath(os.fspath(path.expanduser())))
        if not candidate.name:
            raise ValueError("token storage path must name a file")

        self._path = candidate
        self._parent = candidate.parent
        self._filename = candidate.name
        self._ensure_parent_directory()

    async def get_tokens(self) -> OAuthToken | None:
        """Return the stored tokens, if the file has a ``tokens`` entry."""
        raw = self._read().get("tokens")
        return None if raw is None else OAuthToken.model_validate(raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Persist ``tokens``, keeping any stored client registration."""
        self._write_field("tokens", tokens.model_dump(mode="json"))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return the stored client registration, if the file has one."""
        raw = self._read().get("client_info")
        return None if raw is None else OAuthClientInformationFull.model_validate(raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Persist ``client_info``, keeping any stored tokens."""
        self._write_field("client_info", client_info.model_dump(mode="json"))

    def _ensure_parent_directory(self) -> None:
        with self._open_directory_chain(create_missing=True) as fd:
            self._validate_parent_stat(os.fstat(fd))

    @contextmanager
    def _open_parent_directory(self) -> Iterator[int]:
        with self._open_directory_chain(create_missing=False) as fd:
            self._validate_parent_stat(os.fstat(fd))
            yield fd

    @contextmanager
    def _open_directory_chain(self, *, create_missing: bool) -> Iterator[int]:
        root_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        root_flags |= getattr(os, "O_DIRECTORY", 0)
        try:
            current_fd = os.open(os.path.sep, root_flags)
        except OSError as exc:  # pragma: no cover - an unusable root filesystem is fatal
            raise TokenStorageSecurityError(
                "cannot open filesystem root for secure traversal"
            ) from exc

        try:
            for component in self._parent.parts[1:]:
                next_fd = self._open_directory_component(
                    current_fd, component, create_missing=create_missing
                )
                os.close(current_fd)
                current_fd = next_fd
            yield current_fd
        finally:
            os.close(current_fd)

    def _open_directory_component(
        self, parent_fd: int, component: str, *, create_missing: bool
    ) -> int:
        try:
            before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not create_missing:
                raise TokenStorageSecurityError(
                    f"token storage directory disappeared: {self._parent}"
                ) from None
            try:
                os.mkdir(component, _DIRECTORY_MODE, dir_fd=parent_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise TokenStorageSecurityError(
                    f"cannot create token storage directory component: {component}"
                ) from exc
            try:
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as exc:
                raise TokenStorageSecurityError(
                    f"cannot inspect token storage directory component: {component}"
                ) from exc
        except OSError as exc:
            raise TokenStorageSecurityError(
                f"cannot inspect token storage directory component: {component}"
            ) from exc

        if stat.S_ISLNK(before.st_mode):
            raise TokenStorageSecurityError(
                "token storage path must not contain symbolic-link components"
            )
        if not stat.S_ISDIR(before.st_mode):
            raise TokenStorageSecurityError(
                f"token storage path component is not a directory: {component}"
            )

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(component, flags, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise TokenStorageSecurityError(
                    "token storage path must not contain symbolic-link components"
                ) from exc
            raise TokenStorageSecurityError(
                f"cannot securely open token storage directory component: {component}"
            ) from exc

        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(fd)
            raise TokenStorageSecurityError(
                "token storage directory component changed during secure traversal"
            )
        if not stat.S_ISDIR(opened.st_mode):
            os.close(fd)
            raise TokenStorageSecurityError(
                f"token storage path component is not a directory: {component}"
            )
        return fd

    @staticmethod
    def _validate_parent_stat(metadata: os.stat_result) -> None:
        if not stat.S_ISDIR(metadata.st_mode):
            raise TokenStorageSecurityError("token storage parent is not a directory")
        if metadata.st_uid != os.getuid():
            raise TokenStorageSecurityError("token storage directory is not owned by current user")
        if stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE:
            raise TokenStorageSecurityError(
                "token storage directory must have mode 0700; fix it with chmod 700"
            )

    @staticmethod
    def _validate_file_stat(metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise TokenStorageSecurityError("token storage path is not a regular file")
        if metadata.st_uid != os.getuid():
            raise TokenStorageSecurityError("token storage file is not owned by current user")
        if metadata.st_nlink != 1:
            raise TokenStorageSecurityError("token storage file must not have hard links")
        if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
            raise TokenStorageSecurityError(
                "token storage file must have mode 0600; fix it with chmod 600"
            )

    def _read(self) -> dict[str, object]:
        with self._open_parent_directory() as parent_fd:
            return self._read_from_parent(parent_fd)

    def _read_from_parent(self, parent_fd: int) -> dict[str, object]:
        try:
            before = os.stat(self._filename, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise TokenStorageSecurityError("cannot inspect token storage file") from exc

        if stat.S_ISLNK(before.st_mode):
            raise TokenStorageSecurityError("token storage file must not be a symbolic link")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self._filename, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise TokenStorageSecurityError(
                    "token storage file must not be a symbolic link"
                ) from exc
            raise TokenStorageSecurityError("cannot securely open token storage file") from exc

        try:
            opened = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise TokenStorageSecurityError("token storage file changed during secure open")
            self._validate_file_stat(opened)
            payload = self._read_bounded(fd)
        finally:
            os.close(fd)

        if not payload:
            raise TokenStorageCorruptionError("token storage file is empty")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TokenStorageCorruptionError("token storage file is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise TokenStorageCorruptionError("token storage root must be a JSON object")
        return decoded

    @staticmethod
    def _read_bounded(fd: int) -> bytes:
        chunks: list[bytes] = []
        remaining = _MAX_STORAGE_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_STORAGE_BYTES:
            raise TokenStorageCorruptionError("token storage file exceeds 1 MiB safety limit")
        return payload

    def _write_field(self, field: str, value: object) -> None:
        with self._open_parent_directory() as parent_fd:
            data = self._read_from_parent(parent_fd)
            data[field] = value
            payload = (json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n").encode()
            if len(payload) > _MAX_STORAGE_BYTES:
                raise TokenStorageCorruptionError(
                    "token storage payload exceeds 1 MiB safety limit"
                )
            self._atomic_replace(parent_fd, payload)

    def _atomic_replace(self, parent_fd: int, payload: bytes) -> None:
        temp_name, fd = self._create_temp_file(parent_fd)
        try:
            os.fchmod(fd, _FILE_MODE)
            self._write_all(fd, payload)
            os.fsync(fd)
            os.close(fd)
            fd = -1

            self._reject_unsafe_existing_target(parent_fd)
            os.replace(
                temp_name,
                self._filename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            with suppress(FileNotFoundError):
                os.unlink(temp_name, dir_fd=parent_fd)

    def _create_temp_file(self, parent_fd: int) -> tuple[str, int]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for _ in range(_TEMP_ATTEMPTS):
            name = f".{self._filename}.{secrets.token_hex(16)}.tmp"
            try:
                fd = os.open(name, flags, _FILE_MODE, dir_fd=parent_fd)
            except FileExistsError:
                continue
            else:
                return name, fd
        raise TokenStorageSecurityError("could not allocate a unique token storage temp file")

    def _reject_unsafe_existing_target(self, parent_fd: int) -> None:
        try:
            metadata = os.stat(self._filename, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise TokenStorageSecurityError("token storage file must not be a symbolic link")
        self._validate_file_stat(metadata)

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:  # pragma: no cover - os.write raises on normal failures
                raise OSError("short write while persisting token storage")
            view = view[written:]
