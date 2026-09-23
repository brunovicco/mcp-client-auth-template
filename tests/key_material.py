"""Throwaway signing keys and a policy-compliant directory to hold them in tests.

The private key loader rejects any group- or world-writable ancestor (ADR-0025), which
includes ``/tmp`` on Linux CI runners. Keys are therefore written below the repository's
git-ignored ``build/`` directory instead of pytest's ``tmp_path``.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

_BUILD_ROOT = Path(__file__).resolve().parents[1] / "build"


@contextmanager
def secure_key_dir() -> Iterator[Path]:
    """Yield a fresh 0700 directory whose ancestors satisfy the key-file policy."""
    _BUILD_ROOT.mkdir(mode=0o755, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="pkjwt-", dir=_BUILD_ROOT)).resolve()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def rsa_key(bits: int = 2048) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def ec_key(curve: ec.EllipticCurve | None = None) -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(curve or ec.SECP256R1())


def pem(key: PrivateKeyTypes, *, password: bytes | None = None) -> bytes:
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(password)
        if password is not None
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )


def write_key(path: Path, content: bytes, *, mode: int = 0o600) -> Path:
    path.write_bytes(content)
    os.chmod(path, mode)
    return path
