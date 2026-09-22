"""Load the ``private_key_jwt`` signing key from a secret-mount file, failing closed.

The key never comes from an environment variable. It is read once from a local file with an
SSH ``StrictModes``-style policy (ADR-0025):

- no symbolic link anywhere in the path (each component is ``lstat``-ed and opened with
  ``O_NOFOLLOW`` relative to its already-opened parent, and must not change between the two);
- every directory is owned by the current user or root and is not group/world-writable;
- the key is a regular file with a single link, owned by the current user or root, with no
  group/other permission bits, and at most 64 KiB.

Only unencrypted RSA (>= 2048 bits, signed RS256) and P-256 EC (ES256) keys are accepted, so
the signing algorithm is derived from the key rather than configured. Error messages never
contain key material.
"""

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import SecretStr

SigningAlgorithm = Literal["RS256", "ES256"]

_MAX_KEY_BYTES = 64 * 1024
_MIN_RSA_BITS = 2048
_TRUSTED_OWNERS = frozenset({0})


class PrivateKeySourceError(RuntimeError):
    """Raised when the configured private key file violates the local key policy."""


@dataclass(frozen=True, slots=True)
class SigningKey:
    """A validated PEM signing key and the JWS algorithm it implies."""

    pem: SecretStr
    algorithm: SigningAlgorithm


def _owner_is_trusted(metadata: os.stat_result) -> bool:
    return metadata.st_uid == os.getuid() or metadata.st_uid in _TRUSTED_OWNERS


def _open_no_follow(parent_fd: int, name: str, *, directory: bool) -> tuple[int, os.stat_result]:
    """Open one path component relative to ``parent_fd`` without following symlinks."""
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PrivateKeySourceError("private key path is not accessible") from exc
    if stat.S_ISLNK(before.st_mode):
        raise PrivateKeySourceError("private key path must not contain symbolic links")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise PrivateKeySourceError("private key path must not contain symbolic links") from exc
        raise PrivateKeySourceError("private key path cannot be opened") from exc

    opened = os.fstat(fd)
    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
        os.close(fd)
        raise PrivateKeySourceError("private key path changed while it was being opened")
    return fd, opened


def _validate_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise PrivateKeySourceError("private key path component is not a directory")
    if not _owner_is_trusted(metadata):
        raise PrivateKeySourceError(
            "private key directories must be owned by the current user or root"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise PrivateKeySourceError("private key directories must not be group- or world-writable")


def _validate_key_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise PrivateKeySourceError("private key path is not a regular file")
    if metadata.st_nlink != 1:
        raise PrivateKeySourceError("private key file must not have hard links")
    if not _owner_is_trusted(metadata):
        raise PrivateKeySourceError("private key file must be owned by the current user or root")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PrivateKeySourceError(
            "private key file must not be accessible to group or others; fix it with chmod 600"
        )
    if metadata.st_size > _MAX_KEY_BYTES:
        raise PrivateKeySourceError("private key file exceeds the 64 KiB safety limit")


def _read_key_bytes(path: Path) -> bytes:
    if not path.is_absolute():
        raise PrivateKeySourceError("private key path must be absolute")
    root_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    current_fd = os.open(os.path.sep, root_flags)
    try:
        _validate_directory(os.fstat(current_fd))
        for component in path.parent.parts[1:]:
            next_fd, metadata = _open_no_follow(current_fd, component, directory=True)
            os.close(current_fd)
            current_fd = next_fd
            _validate_directory(metadata)

        key_fd, metadata = _open_no_follow(current_fd, path.name, directory=False)
        try:
            _validate_key_file(metadata)
            payload = os.read(key_fd, _MAX_KEY_BYTES + 1)
        finally:
            os.close(key_fd)
    finally:
        os.close(current_fd)
    if len(payload) > _MAX_KEY_BYTES:
        raise PrivateKeySourceError("private key file exceeds the 64 KiB safety limit")
    return payload


def _algorithm_for(payload: bytes) -> SigningAlgorithm:
    try:
        key = serialization.load_pem_private_key(payload, password=None)
    except TypeError as exc:
        raise PrivateKeySourceError("encrypted private keys are not supported") from exc
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise PrivateKeySourceError("private key file is not a supported PEM private key") from exc
    if isinstance(key, rsa.RSAPrivateKey):
        if key.key_size < _MIN_RSA_BITS:
            raise PrivateKeySourceError("RSA private keys must be at least 2048 bits")
        return "RS256"
    if isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1):
        return "ES256"
    raise PrivateKeySourceError("only RSA (RS256) and P-256 EC (ES256) private keys are supported")


def load_signing_key(path: Path) -> SigningKey:
    """Read and validate the key at ``path``; the PEM is only ever held as a ``SecretStr``."""
    payload = _read_key_bytes(path)
    algorithm = _algorithm_for(payload)
    try:
        pem = payload.decode("ascii")
    except UnicodeDecodeError as exc:  # pragma: no cover - a parsed PEM is ASCII
        raise PrivateKeySourceError("private key file is not a supported PEM private key") from exc
    return SigningKey(pem=SecretStr(pem), algorithm=algorithm)
