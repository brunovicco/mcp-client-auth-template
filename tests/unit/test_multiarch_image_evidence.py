"""Tests for the multi-platform OCI release evidence boundary."""

import json
from pathlib import Path

import pytest
from scripts.prepare_multiarch_image_evidence import (
    MultiarchEvidenceError,
    prepare_multiarch_image_evidence,
)

IMAGE = "ghcr.io/brunovicco/example"
INDEX = f"sha256:{'a' * 64}"
AMD64 = f"sha256:{'b' * 64}"
ARM64 = f"sha256:{'c' * 64}"
COMMIT = "d" * 40
TAG = "v1.2.3"


def _manifest(path: Path, *, arm64_digest: str = ARM64) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "digest": INDEX,
                "manifests": [
                    {
                        "digest": AMD64,
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "platform": {"architecture": "amd64", "os": "linux"},
                    },
                    {
                        "digest": arm64_digest,
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "platform": {"architecture": "arm64", "os": "linux"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_valid_index_emits_exact_platform_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "image-platforms.json"
    _manifest(manifest)

    result = prepare_multiarch_image_evidence(
        manifest,
        output,
        image_name=IMAGE,
        index_digest=INDEX,
        amd64_digest=AMD64,
        arm64_digest=ARM64,
        tag=TAG,
        commit=COMMIT,
    )

    assert result["status"] == "passed"
    document = json.loads(output.read_text())
    assert set(document["platforms"]) == {"linux/amd64", "linux/arm64"}
    assert document["platforms"]["linux/amd64"]["digest"] == AMD64
    assert document["platforms"]["linux/arm64"]["digest"] == ARM64


def test_platform_digest_drift_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _manifest(manifest, arm64_digest=f"sha256:{'e' * 64}")

    with pytest.raises(MultiarchEvidenceError, match="do not match scanned subjects"):
        prepare_multiarch_image_evidence(
            manifest,
            tmp_path / "out.json",
            image_name=IMAGE,
            index_digest=INDEX,
            amd64_digest=AMD64,
            arm64_digest=ARM64,
            tag=TAG,
            commit=COMMIT,
        )


def test_unexpected_platform_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    document = json.loads(manifest.read_text())
    document["manifests"][1]["platform"]["architecture"] = "s390x"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MultiarchEvidenceError, match="unexpected release platform"):
        prepare_multiarch_image_evidence(
            manifest,
            tmp_path / "out.json",
            image_name=IMAGE,
            index_digest=INDEX,
            amd64_digest=AMD64,
            arm64_digest=ARM64,
            tag=TAG,
            commit=COMMIT,
        )
