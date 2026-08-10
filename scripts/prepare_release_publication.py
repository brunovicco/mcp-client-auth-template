#!/usr/bin/env python3
"""Validate and assemble the exact public assets for one secure release."""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from scripts.prepare_release_artifacts import (
    ReleaseArtifactError,
    load_project_identity,
    validate_sdist,
    validate_wheel,
)
from scripts.validate_security_evidence import (
    EvidenceValidationError,
    load_document,
    validate_cyclonedx,
    validate_grype_report,
)

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PACKAGE_CHECKSUM = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/\\]+)$")
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_SBOM_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_BYTES = 128 * 1024 * 1024
EVIDENCE_FILES = {
    "grype.json",
    "image-digest.txt",
    "image.cdx.json",
    "policy.json",
    "source.cdx.json",
}


class ReleasePublicationError(ValueError):
    """Raised when a release bundle violates the publication contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleasePublicationError(f"{path}: could not calculate SHA-256: {exc}") from exc
    return digest.hexdigest()


def _exact_regular_files(directory: Path, expected: set[str]) -> dict[str, Path]:
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise ReleasePublicationError(f"{directory}: could not read release inputs: {exc}") from exc
    if any(path.is_symlink() for path in entries):
        raise ReleasePublicationError(f"{directory}: symbolic links are not allowed")
    actual = {path.name for path in entries}
    if actual != expected:
        raise ReleasePublicationError(
            f"{directory}: expected exactly {sorted(expected)}, got {sorted(actual)}"
        )
    if any(not path.is_file() for path in entries):
        raise ReleasePublicationError(f"{directory}: every release input must be a regular file")
    return {path.name: path for path in entries}


def _bounded(path: Path, *, maximum: int) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReleasePublicationError(f"{path}: could not inspect release input: {exc}") from exc
    if size > maximum:
        raise ReleasePublicationError(f"{path}: release input exceeds {maximum} bytes")


def _validate_package_checksums(path: Path, expected: dict[str, Path]) -> None:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleasePublicationError(f"{path}: invalid package checksum manifest: {exc}") from exc
    parsed: dict[str, str] = {}
    for line in lines:
        match = PACKAGE_CHECKSUM.fullmatch(line)
        if match is None or match.group("name") in parsed:
            raise ReleasePublicationError(f"{path}: malformed or duplicate checksum entry")
        parsed[match.group("name")] = match.group("digest")
    if set(parsed) != set(expected):
        raise ReleasePublicationError(f"{path}: package checksum subjects do not match artifacts")
    for name, artifact in expected.items():
        if parsed[name] != _sha256(artifact):
            raise ReleasePublicationError(f"{path}: checksum mismatch for {name}")


def _validate_policy(path: Path) -> None:
    document = load_document(path)
    if document.get("status") != "passed":
        raise ReleasePublicationError(f"{path}: vulnerability policy status must be passed")
    for field in ("actionable_findings", "unfixed_high_critical_findings"):
        value = document.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ReleasePublicationError(f"{path}: {field} must be a non-negative integer")
    if not isinstance(document.get("approved_exceptions"), list):
        raise ReleasePublicationError(f"{path}: approved_exceptions must be an array")


def _validate_identity(
    root: Path,
    *,
    tag: str,
    repository: str,
    commit: str,
    image_name: str,
    image_digest: str,
) -> tuple[str, str, str]:
    identity = load_project_identity(root)
    expected_tag = f"v{identity.version}"
    if tag != expected_tag:
        raise ReleasePublicationError(f"release tag must be {expected_tag!r}, got {tag!r}")
    if REPOSITORY_NAME.fullmatch(repository) is None:
        raise ReleasePublicationError("repository must use the owner/name form")
    expected_image = f"ghcr.io/{repository.lower()}"
    if image_name != expected_image:
        raise ReleasePublicationError(f"image name must be {expected_image!r}, got {image_name!r}")
    if COMMIT_SHA.fullmatch(commit) is None:
        raise ReleasePublicationError("source commit must be a full lowercase commit SHA")
    if IMAGE_DIGEST.fullmatch(image_digest) is None:
        raise ReleasePublicationError("image digest must be a sha256 digest")
    return identity.name, identity.module, identity.version


def prepare_release_publication(
    root: Path,
    package_dir: Path,
    evidence_dir: Path,
    output: Path,
    *,
    tag: str,
    repository: str,
    commit: str,
    image_name: str,
    image_digest: str,
) -> dict[str, object]:
    """Validate release inputs, copy them, and emit release integrity metadata."""
    try:
        project, module, version = _validate_identity(
            root,
            tag=tag,
            repository=repository,
            commit=commit,
            image_name=image_name,
            image_digest=image_digest,
        )
    except (ReleaseArtifactError, EvidenceValidationError) as exc:
        raise ReleasePublicationError(str(exc)) from exc

    wheel_name = f"{module}-{version}-py3-none-any.whl"
    sdist_name = f"{module}-{version}.tar.gz"
    package_files = _exact_regular_files(
        package_dir,
        {"SHA256SUMS", wheel_name, sdist_name},
    )
    evidence_files = _exact_regular_files(evidence_dir, EVIDENCE_FILES)
    for name, path in evidence_files.items():
        _bounded(path, maximum=MAX_SBOM_BYTES if name.endswith(".cdx.json") else MAX_EVIDENCE_BYTES)

    identity = load_project_identity(root)
    try:
        validate_wheel(package_files[wheel_name], identity=identity)
        validate_sdist(package_files[sdist_name], identity=identity)
        _validate_package_checksums(
            package_files["SHA256SUMS"],
            {wheel_name: package_files[wheel_name], sdist_name: package_files[sdist_name]},
        )
        validate_cyclonedx(evidence_files["source.cdx.json"], expected_component=project)
        validate_cyclonedx(evidence_files["image.cdx.json"], expected_component=project)
        validate_grype_report(
            evidence_files["grype.json"],
            expected_image=f"{project}:release-{commit}",
        )
        _validate_policy(evidence_files["policy.json"])
    except (ReleaseArtifactError, EvidenceValidationError) as exc:
        raise ReleasePublicationError(str(exc)) from exc

    expected_subject = f"{image_name}@{image_digest}\n"
    try:
        actual_subject = evidence_files["image-digest.txt"].read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ReleasePublicationError(
            f"{evidence_files['image-digest.txt']}: invalid image subject: {exc}"
        ) from exc
    if actual_subject != expected_subject:
        raise ReleasePublicationError("image-digest.txt does not match the published image subject")

    if output.is_symlink():
        raise ReleasePublicationError(f"{output}: output directory cannot be a symbolic link")
    try:
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise ReleasePublicationError(f"{output}: output directory must be empty")
    except OSError as exc:
        raise ReleasePublicationError(
            f"{output}: could not prepare output directory: {exc}"
        ) from exc

    inputs = {**package_files, **evidence_files}
    asset_records: dict[str, dict[str, object]] = {}
    for name in sorted(inputs):
        destination = output / name
        shutil.copyfile(inputs[name], destination)
        asset_records[name] = {
            "sha256": _sha256(destination),
            "size": destination.stat().st_size,
        }

    manifest: dict[str, Any] = {
        "assets": asset_records,
        "image": {
            "digest": image_digest,
            "name": image_name,
            "reference": f"{image_name}@{image_digest}",
            "tags": [tag, f"sha-{commit}"],
        },
        "project": project,
        "schema_version": 1,
        "source_commit": commit,
        "source_repository": repository,
        "tag": tag,
        "version": version,
    }
    manifest_path = output / "release-manifest.json"
    manifest_path.write_text(
        f"{json.dumps(manifest, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )

    checksummed = [*sorted(inputs), manifest_path.name]
    checksums = "".join(f"{_sha256(output / name)}  {name}\n" for name in checksummed)
    (output / "RELEASE_SHA256SUMS").write_text(checksums, encoding="ascii")
    return {
        "asset_count": len(checksummed),
        "image": f"{image_name}@{image_digest}",
        "project": project,
        "status": "passed",
        "tag": tag,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--image-digest", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare_release_publication(
            args.root,
            args.package_dir,
            args.evidence_dir,
            args.output,
            tag=args.tag,
            repository=args.repository,
            commit=args.commit,
            image_name=args.image_name,
            image_digest=args.image_digest,
        )
    except ReleasePublicationError as exc:
        print(f"Release publication validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
