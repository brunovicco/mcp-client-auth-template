"""Tests for the final secure-release publication boundary."""

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.prepare_release_publication import (
    ReleasePublicationError,
    prepare_release_publication,
)

PROJECT = "example-package"
MODULE = "example_package"
VERSION = "1.2.3"
REPOSITORY = "brunovicco/example-package"
COMMIT = "b" * 40
DIGEST = f"sha256:{'a' * 64}"
IMAGE_NAME = f"ghcr.io/{REPOSITORY}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{PROJECT}"\nversion = "{VERSION}"\n',
        encoding="utf-8",
    )


def _wheel(path: Path) -> None:
    dist_info = f"{MODULE}-{VERSION}.dist-info"
    metadata = f"Metadata-Version: 2.4\nName: {PROJECT}\nVersion: {VERSION}\n"
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr(f"{MODULE}/__init__.py", '__version__ = "1.2.3"\n')
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr(f"{dist_info}/RECORD", "")


def _tar_entry(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.mtime = 1_580_601_600
    member.mode = 0o644
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def _sdist(path: Path) -> None:
    root = f"{MODULE}-{VERSION}"
    files = {
        ".gitignore": b"dist/\n",
        "CHANGELOG.md": b"# Changelog\n",
        "LICENSE": b"MIT\n",
        "PKG-INFO": f"Metadata-Version: 2.4\nName: {PROJECT}\nVersion: {VERSION}\n".encode(),
        "README.md": b"# Example\n",
        "README.pt-BR.md": b"# Exemplo\n",
        "pyproject.toml": f'[project]\nname = "{PROJECT}"\nversion = "{VERSION}"\n'.encode(),
        f"src/{MODULE}/__init__.py": b'value = "safe"\n',
    }
    with tarfile.open(path, mode="w:gz") as archive:
        for name, content in sorted(files.items()):
            _tar_entry(archive, f"{root}/{name}", content)


def _sbom() -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:12345678-1234-4123-8123-123456789abc",
        "version": 1,
        "components": [
            {"name": PROJECT, "version": VERSION, "type": "application"},
            {"name": "pydantic", "version": "2.11.0", "type": "library"},
        ],
    }


def _inputs(root: Path) -> tuple[Path, Path, Path]:
    _project(root)
    packages = root / "packages"
    evidence = root / "evidence"
    output = root / "release"
    packages.mkdir()
    evidence.mkdir()

    wheel = packages / f"{MODULE}-{VERSION}-py3-none-any.whl"
    sdist = packages / f"{MODULE}-{VERSION}.tar.gz"
    _wheel(wheel)
    _sdist(sdist)
    (packages / "SHA256SUMS").write_text(
        f"{_sha256(wheel)}  {wheel.name}\n{_sha256(sdist)}  {sdist.name}\n",
        encoding="ascii",
    )
    for name in ("source.cdx.json", "image.cdx.json"):
        (evidence / name).write_text(json.dumps(_sbom()), encoding="utf-8")
    report = {
        "descriptor": {"name": "grype", "version": "0.116.1"},
        "matches": [],
        "source": {
            "type": "image",
            "target": {"userInput": f"{PROJECT}:release-{COMMIT}"},
        },
    }
    (evidence / "grype.json").write_text(json.dumps(report), encoding="utf-8")
    policy = {
        "actionable_findings": 0,
        "approved_exceptions": [],
        "effective_date": "2026-08-09",
        "status": "passed",
        "unfixed_high_critical_findings": 0,
    }
    (evidence / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    (evidence / "image-digest.txt").write_text(
        f"{IMAGE_NAME}@{DIGEST}\n",
        encoding="ascii",
    )
    return packages, evidence, output


def _prepare(root: Path) -> dict[str, object]:
    packages, evidence, output = _inputs(root)
    return prepare_release_publication(
        root,
        packages,
        evidence,
        output,
        tag=f"v{VERSION}",
        repository=REPOSITORY,
        commit=COMMIT,
        image_name=IMAGE_NAME,
        image_digest=DIGEST,
    )


def test_valid_release_emits_manifest_and_complete_checksums(tmp_path: Path) -> None:
    result = _prepare(tmp_path)

    assert result["asset_count"] == 9
    assert result["status"] == "passed"
    manifest = json.loads((tmp_path / "release/release-manifest.json").read_text())
    assert manifest["image"]["reference"] == f"{IMAGE_NAME}@{DIGEST}"
    assert manifest["source_commit"] == COMMIT
    assert len((tmp_path / "release/RELEASE_SHA256SUMS").read_text().splitlines()) == 9


def test_unexpected_evidence_file_is_rejected(tmp_path: Path) -> None:
    packages, evidence, output = _inputs(tmp_path)
    (evidence / "tokens.json").write_text("secret", encoding="utf-8")

    with pytest.raises(ReleasePublicationError, match="expected exactly"):
        prepare_release_publication(
            tmp_path,
            packages,
            evidence,
            output,
            tag=f"v{VERSION}",
            repository=REPOSITORY,
            commit=COMMIT,
            image_name=IMAGE_NAME,
            image_digest=DIGEST,
        )


def test_package_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    packages, evidence, output = _inputs(tmp_path)
    manifest = packages / "SHA256SUMS"
    contents = manifest.read_text(encoding="ascii")
    replacement = "0" if contents[0] != "0" else "1"
    manifest.write_text(f"{replacement}{contents[1:]}", encoding="ascii")

    with pytest.raises(ReleasePublicationError, match="checksum mismatch"):
        prepare_release_publication(
            tmp_path,
            packages,
            evidence,
            output,
            tag=f"v{VERSION}",
            repository=REPOSITORY,
            commit=COMMIT,
            image_name=IMAGE_NAME,
            image_digest=DIGEST,
        )


def test_image_subject_must_match_published_digest(tmp_path: Path) -> None:
    packages, evidence, output = _inputs(tmp_path)
    (evidence / "image-digest.txt").write_text(
        f"{IMAGE_NAME}@sha256:{'c' * 64}\n",
        encoding="ascii",
    )

    with pytest.raises(ReleasePublicationError, match="does not match"):
        prepare_release_publication(
            tmp_path,
            packages,
            evidence,
            output,
            tag=f"v{VERSION}",
            repository=REPOSITORY,
            commit=COMMIT,
            image_name=IMAGE_NAME,
            image_digest=DIGEST,
        )


def test_failed_vulnerability_policy_is_rejected(tmp_path: Path) -> None:
    packages, evidence, output = _inputs(tmp_path)
    policy_path = evidence / "policy.json"
    policy = json.loads(policy_path.read_text())
    policy["status"] = "failed"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ReleasePublicationError, match="status must be passed"):
        prepare_release_publication(
            tmp_path,
            packages,
            evidence,
            output,
            tag=f"v{VERSION}",
            repository=REPOSITORY,
            commit=COMMIT,
            image_name=IMAGE_NAME,
            image_digest=DIGEST,
        )


def test_release_tag_must_match_project_version(tmp_path: Path) -> None:
    packages, evidence, output = _inputs(tmp_path)

    with pytest.raises(ReleasePublicationError, match="release tag must be"):
        prepare_release_publication(
            tmp_path,
            packages,
            evidence,
            output,
            tag="v9.9.9",
            repository=REPOSITORY,
            commit=COMMIT,
            image_name=IMAGE_NAME,
            image_digest=DIGEST,
        )


def test_evidence_symlinks_are_rejected(tmp_path: Path) -> None:
    packages, evidence, output = _inputs(tmp_path)
    (evidence / "source.cdx.json").unlink()
    (evidence / "source.cdx.json").symlink_to(evidence / "image.cdx.json")

    with pytest.raises(ReleasePublicationError, match="symbolic links"):
        prepare_release_publication(
            tmp_path,
            packages,
            evidence,
            output,
            tag=f"v{VERSION}",
            repository=REPOSITORY,
            commit=COMMIT,
            image_name=IMAGE_NAME,
            image_digest=DIGEST,
        )
