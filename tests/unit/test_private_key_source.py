"""Fail-closed policy tests for the private_key_jwt signing-key file (ADR-0025)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from mcp_client_auth_template.adapters.private_key_source import (
    PrivateKeySourceError,
    load_signing_key,
)
from tests.key_material import ec_key, pem, rsa_key, secure_key_dir, write_key


@pytest.fixture
def key_dir() -> Iterator[Path]:
    with secure_key_dir() as directory:
        yield directory


@pytest.fixture(scope="module")
def rsa_pem() -> bytes:
    return pem(rsa_key())


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_accepts_owner_only_rsa_key_and_derives_rs256(
    key_dir: Path, rsa_pem: bytes, mode: int
) -> None:
    key = load_signing_key(write_key(key_dir / "client.pem", rsa_pem, mode=mode))

    assert key.algorithm == "RS256"
    assert key.pem.get_secret_value().encode() == rsa_pem
    assert "PRIVATE KEY" not in repr(key)


def test_accepts_p256_key_and_derives_es256(key_dir: Path) -> None:
    key = load_signing_key(write_key(key_dir / "client.pem", pem(ec_key())))

    assert key.algorithm == "ES256"


@pytest.mark.parametrize("mode", [0o640, 0o644, 0o604, 0o660])
def test_rejects_group_or_world_accessible_key(key_dir: Path, rsa_pem: bytes, mode: int) -> None:
    path = write_key(key_dir / "client.pem", rsa_pem, mode=mode)

    with pytest.raises(PrivateKeySourceError, match="group or others"):
        load_signing_key(path)


def test_rejects_symlinked_key_file(key_dir: Path, rsa_pem: bytes) -> None:
    target = write_key(key_dir / "real.pem", rsa_pem)
    link = key_dir / "client.pem"
    link.symlink_to(target)

    with pytest.raises(PrivateKeySourceError, match="symbolic links"):
        load_signing_key(link)


def test_rejects_symlinked_directory_component(key_dir: Path, rsa_pem: bytes) -> None:
    real = key_dir / "real"
    real.mkdir(mode=0o700)
    write_key(real / "client.pem", rsa_pem)
    (key_dir / "mounted").symlink_to(real, target_is_directory=True)

    with pytest.raises(PrivateKeySourceError, match="symbolic links"):
        load_signing_key(key_dir / "mounted" / "client.pem")


@pytest.mark.parametrize("mode", [0o770, 0o777, 0o722])
def test_rejects_group_or_world_writable_directory(
    key_dir: Path, rsa_pem: bytes, mode: int
) -> None:
    exposed = key_dir / "exposed"
    exposed.mkdir()
    path = write_key(exposed / "client.pem", rsa_pem)
    os.chmod(exposed, mode)

    with pytest.raises(PrivateKeySourceError, match="group- or world-writable"):
        load_signing_key(path)


def test_rejects_hard_linked_key(key_dir: Path, rsa_pem: bytes) -> None:
    path = write_key(key_dir / "client.pem", rsa_pem)
    os.link(path, key_dir / "second-name.pem")

    with pytest.raises(PrivateKeySourceError, match="hard links"):
        load_signing_key(path)


def test_rejects_non_regular_file(key_dir: Path) -> None:
    directory = key_dir / "client.pem"
    directory.mkdir(mode=0o700)

    with pytest.raises(PrivateKeySourceError):
        load_signing_key(directory)


def test_rejects_oversized_file(key_dir: Path) -> None:
    path = write_key(key_dir / "client.pem", b"A" * (64 * 1024 + 1))

    with pytest.raises(PrivateKeySourceError, match="64 KiB"):
        load_signing_key(path)


def test_rejects_relative_and_missing_paths(key_dir: Path) -> None:
    with pytest.raises(PrivateKeySourceError, match="absolute"):
        load_signing_key(Path("client.pem"))
    with pytest.raises(PrivateKeySourceError, match="not accessible"):
        load_signing_key(key_dir / "absent.pem")


def test_rejects_encrypted_key(key_dir: Path) -> None:
    path = write_key(key_dir / "client.pem", pem(ec_key(), password=b"passphrase"))

    with pytest.raises(PrivateKeySourceError, match="encrypted"):
        load_signing_key(path)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(lambda: pem(rsa_key(1024)), id="rsa-1024"),
        pytest.param(lambda: pem(ec_key(ec.SECP384R1())), id="ec-p384"),
        pytest.param(lambda: pem(ed25519.Ed25519PrivateKey.generate()), id="ed25519"),
    ],
)
def test_rejects_unsupported_key_types(key_dir: Path, content: object) -> None:
    assert callable(content)
    path = write_key(key_dir / "client.pem", content())

    with pytest.raises(PrivateKeySourceError, match=r"2048 bits|only RSA"):
        load_signing_key(path)


def test_rejects_garbage_without_echoing_it(key_dir: Path) -> None:
    marker = "-----BEGIN PRIVATE KEY-----\nnot-really-a-key-4b1d\n-----END PRIVATE KEY-----\n"
    path = write_key(key_dir / "client.pem", marker.encode())

    with pytest.raises(PrivateKeySourceError) as raised:
        load_signing_key(path)

    rendered = f"{raised.value!s} {raised.value!r} {raised.value.__cause__!s}"
    assert "not-really-a-key-4b1d" not in rendered
    assert "not-really-a-key-4b1d" not in str(raised.value)
