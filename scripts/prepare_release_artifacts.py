#!/usr/bin/env python3
"""Validate reproducible Python release artifacts and emit their SHA-256 manifest."""

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any, cast

ALLOWED_SDIST_ROOT_FILES = {
    ".gitignore",
    "CHANGELOG.md",
    "LICENSE",
    "PKG-INFO",
    "README.md",
    "README.pt-BR.md",
    "pyproject.toml",
}
MAX_ARCHIVE_MEMBERS = 10_000
MAX_METADATA_BYTES = 1024 * 1024


class ReleaseArtifactError(ValueError):
    """Raised when a release artifact violates the package integrity contract."""


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """Expected package identity loaded from pyproject.toml."""

    name: str
    module: str
    version: str


def _required_string(document: dict[str, Any], field: str, *, context: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ReleaseArtifactError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def load_project_identity(root: Path) -> ProjectIdentity:
    """Load the package name and version that a release tag must identify."""
    path = root / "pyproject.toml"
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseArtifactError(f"{path}: invalid project metadata: {exc}") from exc
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseArtifactError(f"{path}: project table is required")
    project = cast(dict[str, Any], project)
    name = _required_string(project, "name", context=str(path))
    version = _required_string(project, "version", context=str(path))
    module = re.sub(r"[-.]+", "_", name).lower()
    if re.fullmatch(r"[a-z][a-z0-9_]*", module) is None:
        raise ReleaseArtifactError(f"{path}: project name cannot map to a safe module path")
    return ProjectIdentity(name=name, module=module, version=version)


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_metadata(
    raw: bytes,
    *,
    identity: ProjectIdentity,
    context: str,
) -> None:
    metadata = BytesParser().parsebytes(raw)
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not isinstance(name, str) or _canonical_name(name) != _canonical_name(identity.name):
        raise ReleaseArtifactError(f"{context}: package Name does not match {identity.name!r}")
    if version != identity.version:
        raise ReleaseArtifactError(
            f"{context}: package Version must be {identity.version!r}, got {version!r}"
        )


def _safe_parts(name: str, *, context: str) -> tuple[str, ...]:
    clean = name[:-1] if name.endswith("/") else name
    parts = tuple(clean.split("/"))
    if (
        not clean
        or clean.startswith("/")
        or "\\" in clean
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ReleaseArtifactError(f"{context}: unsafe archive path {name!r}")
    return parts


def validate_wheel(path: Path, *, identity: ProjectIdentity) -> None:
    """Validate wheel identity, member safety, and content boundary."""
    expected_name = f"{identity.module}-{identity.version}-py3-none-any.whl"
    if path.name != expected_name:
        raise ReleaseArtifactError(f"{path}: wheel filename must be {expected_name}")
    dist_info = f"{identity.module}-{identity.version}.dist-info"
    metadata_name = f"{dist_info}/METADATA"
    seen: set[str] = set()
    metadata_bytes: bytes | None = None
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseArtifactError(f"{path}: wheel has too many members")
            for member in members:
                parts = _safe_parts(member.filename, context=str(path))
                normalized = "/".join(parts)
                if normalized in seen:
                    raise ReleaseArtifactError(f"{path}: duplicate wheel member {normalized!r}")
                seen.add(normalized)
                file_type = (member.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    raise ReleaseArtifactError(f"{path}: symbolic links are not allowed")
                if parts[0] not in {identity.module, dist_info}:
                    raise ReleaseArtifactError(
                        f"{path}: unexpected wheel content {member.filename!r}"
                    )
                if normalized == metadata_name:
                    if member.file_size > MAX_METADATA_BYTES:
                        raise ReleaseArtifactError(f"{path}: wheel METADATA is too large")
                    metadata_bytes = archive.read(member)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseArtifactError(f"{path}: invalid wheel archive: {exc}") from exc
    if metadata_bytes is None:
        raise ReleaseArtifactError(f"{path}: wheel METADATA is missing")
    if not any(name.startswith(f"{identity.module}/") for name in seen):
        raise ReleaseArtifactError(f"{path}: wheel package content is missing")
    _validate_metadata(metadata_bytes, identity=identity, context=metadata_name)


def validate_sdist(path: Path, *, identity: ProjectIdentity) -> None:
    """Validate sdist identity, safe members, and the explicit source allowlist."""
    expected_name = f"{identity.module}-{identity.version}.tar.gz"
    if path.name != expected_name:
        raise ReleaseArtifactError(f"{path}: sdist filename must be {expected_name}")
    expected_root = f"{identity.module}-{identity.version}"
    seen: set[str] = set()
    root_files: set[str] = set()
    source_seen = False
    metadata_bytes: bytes | None = None
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseArtifactError(f"{path}: sdist has too many members")
            for member in members:
                parts = _safe_parts(member.name, context=str(path))
                normalized = "/".join(parts)
                if normalized in seen:
                    raise ReleaseArtifactError(f"{path}: duplicate sdist member {normalized!r}")
                seen.add(normalized)
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ReleaseArtifactError(
                        f"{path}: only regular files/directories are allowed"
                    )
                if parts[0] != expected_root:
                    raise ReleaseArtifactError(f"{path}: unexpected archive root {parts[0]!r}")
                relative = parts[1:]
                if not relative:
                    continue
                if len(relative) == 1 and relative[0] in ALLOWED_SDIST_ROOT_FILES:
                    root_files.add(relative[0])
                elif len(relative) >= 2 and relative[:2] == ("src", identity.module):
                    source_seen = source_seen or member.isfile()
                else:
                    raise ReleaseArtifactError(
                        f"{path}: unexpected sdist content {'/'.join(relative)!r}"
                    )
                if relative == ("PKG-INFO",):
                    if member.size > MAX_METADATA_BYTES:
                        raise ReleaseArtifactError(f"{path}: PKG-INFO is too large")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ReleaseArtifactError(f"{path}: could not read PKG-INFO")
                    metadata_bytes = extracted.read()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseArtifactError(f"{path}: invalid sdist archive: {exc}") from exc
    missing = ALLOWED_SDIST_ROOT_FILES - root_files
    if missing:
        raise ReleaseArtifactError(f"{path}: required sdist files are missing: {sorted(missing)}")
    if not source_seen:
        raise ReleaseArtifactError(f"{path}: sdist package source is missing")
    if metadata_bytes is None:
        raise ReleaseArtifactError(f"{path}: PKG-INFO is missing")
    _validate_metadata(metadata_bytes, identity=identity, context="PKG-INFO")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_paths(directory: Path, *, identity: ProjectIdentity) -> dict[str, Path]:
    try:
        all_paths = sorted(directory.iterdir())
    except OSError as exc:
        raise ReleaseArtifactError(f"{directory}: could not read build directory: {exc}") from exc
    if any(path.is_symlink() for path in all_paths):
        raise ReleaseArtifactError(f"{directory}: artifact symlinks are not allowed")
    sentinel = directory / ".gitignore"
    if sentinel in all_paths:
        try:
            sentinel_content = sentinel.read_text(encoding="ascii")
        except OSError as exc:
            raise ReleaseArtifactError(f"{sentinel}: could not read uv sentinel: {exc}") from exc
        if sentinel_content != "*":
            raise ReleaseArtifactError(f"{sentinel}: unexpected uv sentinel content")
    paths = [path for path in all_paths if path != sentinel and path.is_file()]
    non_files = [path.name for path in all_paths if path != sentinel and not path.is_file()]
    if non_files:
        raise ReleaseArtifactError(f"{directory}: unexpected build entries: {non_files}")
    expected = {
        f"{identity.module}-{identity.version}-py3-none-any.whl",
        f"{identity.module}-{identity.version}.tar.gz",
    }
    actual = {path.name for path in paths}
    if actual != expected:
        raise ReleaseArtifactError(
            f"{directory}: expected exactly {sorted(expected)}, got {sorted(actual)}"
        )
    return {path.name: path for path in paths}


def prepare_release_artifacts(
    root: Path,
    first_build: Path,
    second_build: Path,
    output: Path,
    *,
    tag: str,
) -> dict[str, object]:
    """Validate two builds, copy one trusted set, and write SHA256SUMS."""
    identity = load_project_identity(root)
    expected_tag = f"v{identity.version}"
    if tag != expected_tag:
        raise ReleaseArtifactError(f"release tag must be {expected_tag!r}, got {tag!r}")

    first = _artifact_paths(first_build, identity=identity)
    second = _artifact_paths(second_build, identity=identity)
    for artifacts in (first, second):
        wheel_name = f"{identity.module}-{identity.version}-py3-none-any.whl"
        validate_wheel(artifacts[wheel_name], identity=identity)
        validate_sdist(artifacts[f"{identity.module}-{identity.version}.tar.gz"], identity=identity)

    digests: dict[str, str] = {}
    for name in sorted(first):
        first_digest = _sha256(first[name])
        second_digest = _sha256(second[name])
        if first_digest != second_digest:
            raise ReleaseArtifactError(
                f"{name}: repeated builds are not byte-for-byte reproducible"
            )
        digests[name] = first_digest

    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ReleaseArtifactError(f"{output}: output directory must be empty")
    for name in sorted(first):
        shutil.copyfile(first[name], output / name)
    manifest = "".join(f"{digests[name]}  {name}\n" for name in sorted(digests))
    (output / "SHA256SUMS").write_text(manifest, encoding="ascii")
    return {
        "artifact_count": len(digests),
        "digests": digests,
        "project": identity.name,
        "status": "passed",
        "tag": tag,
        "version": identity.version,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--first-build", type=Path, required=True)
    parser.add_argument("--second-build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare_release_artifacts(
            args.root,
            args.first_build,
            args.second_build,
            args.output,
            tag=args.tag,
        )
    except ReleaseArtifactError as exc:
        print(f"Release artifact validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
