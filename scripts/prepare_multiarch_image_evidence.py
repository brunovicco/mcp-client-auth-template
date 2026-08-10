#!/usr/bin/env python3
"""Validate a published two-platform OCI index and emit minimized release evidence."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_PLATFORMS = {
    "linux/amd64": ("linux", "amd64"),
    "linux/arm64": ("linux", "arm64"),
}


class MultiarchEvidenceError(ValueError):
    """Raised when the published OCI index does not match the release contract."""


def _load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiarchEvidenceError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise MultiarchEvidenceError(f"{path}: manifest must be a JSON object")
    return cast(dict[str, Any], document)


def _digest(value: str, label: str) -> str:
    if DIGEST.fullmatch(value) is None:
        raise MultiarchEvidenceError(f"{label} must be a sha256 digest")
    return value


def prepare_multiarch_image_evidence(
    manifest_path: Path,
    output: Path,
    *,
    image_name: str,
    index_digest: str,
    amd64_digest: str,
    arm64_digest: str,
    tag: str,
    commit: str,
) -> dict[str, object]:
    """Validate exact platforms/digests and write the canonical platform evidence."""
    index_digest = _digest(index_digest, "index digest")
    expected_digests = {
        "linux/amd64": _digest(amd64_digest, "amd64 digest"),
        "linux/arm64": _digest(arm64_digest, "arm64 digest"),
    }
    if expected_digests["linux/amd64"] == expected_digests["linux/arm64"]:
        raise MultiarchEvidenceError("amd64 and arm64 digests must be different")
    if COMMIT.fullmatch(commit) is None:
        raise MultiarchEvidenceError("source commit must be a full lowercase commit SHA")
    if not tag.startswith("v") or not tag[1:]:
        raise MultiarchEvidenceError("release tag must be a non-empty v-prefixed tag")
    repository = image_name.removeprefix("ghcr.io/")
    if not image_name.startswith("ghcr.io/") or "@" in image_name or ":" in repository:
        raise MultiarchEvidenceError("image name must be an untagged ghcr.io repository")

    manifest = _load(manifest_path)
    if manifest.get("digest") != index_digest:
        raise MultiarchEvidenceError("registry index digest does not match the published subject")
    descriptors = manifest.get("manifests")
    if not isinstance(descriptors, list) or len(descriptors) != 2:
        raise MultiarchEvidenceError("registry index must contain exactly two platform manifests")

    found: dict[str, str] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise MultiarchEvidenceError("every index descriptor must be an object")
        platform = descriptor.get("platform")
        digest = descriptor.get("digest")
        if not isinstance(platform, dict) or not isinstance(digest, str):
            raise MultiarchEvidenceError("every descriptor requires platform and digest")
        os_name = platform.get("os")
        architecture = platform.get("architecture")
        key = f"{os_name}/{architecture}"
        if key not in EXPECTED_PLATFORMS:
            raise MultiarchEvidenceError(f"unexpected release platform: {key}")
        if key in found:
            raise MultiarchEvidenceError(f"duplicate release platform: {key}")
        found[key] = _digest(digest, f"{key} digest")

    if found != expected_digests:
        raise MultiarchEvidenceError("registry platform digests do not match scanned subjects")

    platform_records = {
        platform: {
            "architecture": architecture,
            "digest": found[platform],
            "os": os_name,
            "reference": f"{image_name}@{found[platform]}",
            "tags": [f"{tag}-{architecture}", f"sha-{commit}-{architecture}"],
        }
        for platform, (os_name, architecture) in EXPECTED_PLATFORMS.items()
    }
    evidence: dict[str, object] = {
        "index": {
            "digest": index_digest,
            "reference": f"{image_name}@{index_digest}",
            "tags": [tag, f"sha-{commit}"],
        },
        "platforms": platform_records,
        "schema_version": 1,
    }

    if output.is_symlink():
        raise MultiarchEvidenceError(f"{output}: output cannot be a symbolic link")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{json.dumps(evidence, indent=2, sort_keys=True)}\n", encoding="utf-8")
    except OSError as exc:
        raise MultiarchEvidenceError(f"{output}: could not write evidence: {exc}") from exc

    return {
        "index_digest": index_digest,
        "platforms": sorted(platform_records),
        "status": "passed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--index-digest", required=True)
    parser.add_argument("--amd64-digest", required=True)
    parser.add_argument("--arm64-digest", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare_multiarch_image_evidence(
            args.manifest,
            args.output,
            image_name=args.image_name,
            index_digest=args.index_digest,
            amd64_digest=args.amd64_digest,
            arm64_digest=args.arm64_digest,
            tag=args.tag,
            commit=args.commit,
        )
    except MultiarchEvidenceError as exc:
        print(f"Multi-platform image evidence validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
