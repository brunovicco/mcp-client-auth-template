"""Tests for reproducible release artifact preparation."""

import io
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.prepare_release_artifacts import (
    MAX_METADATA_BYTES,
    ReleaseArtifactError,
    prepare_release_artifacts,
)

PROJECT_NAME = "example-package"
MODULE = "example_package"
VERSION = "1.2.3"


def _project(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{PROJECT_NAME}"\nversion = "{VERSION}"\n',
        encoding="utf-8",
    )


def _wheel(
    path: Path,
    *,
    extra: str | None = None,
    metadata_padding: int = 0,
    name: str = PROJECT_NAME,
) -> None:
    dist_info = f"{MODULE}-{VERSION}.dist-info"
    metadata = f"Metadata-Version: 2.4\nName: {name}\nVersion: {VERSION}\n" + (
        "X" * metadata_padding
    )
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr(f"{MODULE}/__init__.py", '__version__ = "1.2.3"\n')
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr(f"{dist_info}/RECORD", "")
        if extra is not None:
            archive.writestr(extra, "unexpected")


def _tar_entry(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.mtime = 1_580_601_600
    member.mode = 0o644
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def _sdist(path: Path, *, extra: str | None = None, source: bytes = b"value = 1\n") -> None:
    root = f"{MODULE}-{VERSION}"
    files = {
        ".gitignore": b"dist/\n",
        "CHANGELOG.md": b"# Changelog\n",
        "LICENSE": b"MIT\n",
        "PKG-INFO": (f"Metadata-Version: 2.4\nName: {PROJECT_NAME}\nVersion: {VERSION}\n").encode(),
        "README.md": b"# Example\n",
        "README.pt-BR.md": b"# Exemplo\n",
        "pyproject.toml": (f'[project]\nname = "{PROJECT_NAME}"\nversion = "{VERSION}"\n').encode(),
        f"src/{MODULE}/__init__.py": source,
    }
    if extra is not None:
        files[extra] = b"unexpected"
    with tarfile.open(path, mode="w:gz") as archive:
        for name, content in sorted(files.items()):
            _tar_entry(archive, f"{root}/{name}", content)


def _build(
    directory: Path,
    *,
    extra_sdist: str | None = None,
    source: bytes = b"value = 1\n",
) -> None:
    directory.mkdir()
    _wheel(directory / f"{MODULE}-{VERSION}-py3-none-any.whl")
    _sdist(
        directory / f"{MODULE}-{VERSION}.tar.gz",
        extra=extra_sdist,
        source=source,
    )


def _pair(tmp_path: Path, *, extra_sdist: str | None = None) -> tuple[Path, Path, Path]:
    first = tmp_path / "first"
    second = tmp_path / "second"
    output = tmp_path / "dist"
    _build(first, extra_sdist=extra_sdist)
    shutil.copytree(first, second)
    return first, second, output


def test_reproducible_artifacts_emit_checksums(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path)

    result = prepare_release_artifacts(tmp_path, first, second, output, tag="v1.2.3")

    assert result["artifact_count"] == 2
    assert result["status"] == "passed"
    checksums = (output / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert len(checksums) == 2
    assert all("  example_package-1.2.3" in line for line in checksums)


def test_release_tag_must_match_project_version(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path)

    with pytest.raises(ReleaseArtifactError, match="release tag must be"):
        prepare_release_artifacts(tmp_path, first, second, output, tag="v9.9.9")


def test_uv_gitignore_sentinel_must_have_exact_content(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path)
    (first / ".gitignore").write_text("unexpected", encoding="ascii")
    (second / ".gitignore").write_text("unexpected", encoding="ascii")

    with pytest.raises(ReleaseArtifactError, match="unexpected uv sentinel content"):
        prepare_release_artifacts(tmp_path, first, second, output, tag="v1.2.3")


def test_non_reproducible_build_is_rejected(tmp_path: Path) -> None:
    _project(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _build(first)
    _build(second, source=b"value = 2\n")

    with pytest.raises(ReleaseArtifactError, match="not byte-for-byte reproducible"):
        prepare_release_artifacts(tmp_path, first, second, tmp_path / "dist", tag="v1.2.3")


def test_sdist_rejects_local_assistant_content(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path, extra_sdist="CLAUDE.md")

    with pytest.raises(ReleaseArtifactError, match="unexpected sdist content"):
        prepare_release_artifacts(tmp_path, first, second, output, tag="v1.2.3")


def test_wheel_rejects_content_outside_package_boundary(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path)
    _wheel(first / f"{MODULE}-{VERSION}-py3-none-any.whl", extra="tokens.json")
    shutil.copyfile(
        first / f"{MODULE}-{VERSION}-py3-none-any.whl",
        second / f"{MODULE}-{VERSION}-py3-none-any.whl",
    )

    with pytest.raises(ReleaseArtifactError, match="unexpected wheel content"):
        prepare_release_artifacts(tmp_path, first, second, output, tag="v1.2.3")


def test_wheel_metadata_must_match_project_identity(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path)
    _wheel(first / f"{MODULE}-{VERSION}-py3-none-any.whl", name="another-project")
    shutil.copyfile(
        first / f"{MODULE}-{VERSION}-py3-none-any.whl",
        second / f"{MODULE}-{VERSION}-py3-none-any.whl",
    )

    with pytest.raises(ReleaseArtifactError, match="package Name"):
        prepare_release_artifacts(tmp_path, first, second, output, tag="v1.2.3")


def test_wheel_metadata_size_is_bounded(tmp_path: Path) -> None:
    _project(tmp_path)
    first, second, output = _pair(tmp_path)
    wheel_name = f"{MODULE}-{VERSION}-py3-none-any.whl"
    _wheel(first / wheel_name, metadata_padding=MAX_METADATA_BYTES)
    shutil.copyfile(first / wheel_name, second / wheel_name)

    with pytest.raises(ReleaseArtifactError, match="METADATA is too large"):
        prepare_release_artifacts(tmp_path, first, second, output, tag="v1.2.3")
