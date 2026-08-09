#!/usr/bin/env python3
"""Validate CycloneDX SBOMs and Grype vulnerability evidence."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

EXPECTED_GRYPE_VERSION = "0.116.1"
SUPPORTED_CYCLONEDX_VERSIONS = {"1.5", "1.6", "1.7"}
SERIAL_NUMBER = re.compile(
    r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class EvidenceValidationError(ValueError):
    """Raised when generated supply-chain evidence violates its contract."""


def load_document(path: Path) -> dict[str, Any]:
    """Load one JSON evidence document as an object."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise EvidenceValidationError(f"{path}: evidence document must be an object")
    return cast(dict[str, Any], document)


def _normalized_name(value: str) -> str:
    """Normalize package names for Python distribution comparisons."""
    return re.sub(r"[-_.]+", "-", value).lower()


def _component_names(document: dict[str, Any]) -> set[str]:
    """Collect normalized names from top-level and nested CycloneDX components."""
    pending: list[object] = []
    components = document.get("components")
    if isinstance(components, list):
        pending.extend(components)
    metadata = document.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("component"), dict):
        pending.append(metadata["component"])

    names: set[str] = set()
    while pending:
        component = pending.pop()
        if not isinstance(component, dict):
            continue
        name = component.get("name")
        if isinstance(name, str) and name:
            names.add(_normalized_name(name))
        nested = component.get("components")
        if isinstance(nested, list):
            pending.extend(nested)
    return names


def validate_cyclonedx(path: Path, *, expected_component: str) -> dict[str, object]:
    """Validate one CycloneDX JSON SBOM and return minimized evidence."""
    document = load_document(path)
    if document.get("bomFormat") != "CycloneDX":
        raise EvidenceValidationError(f"{path}: bomFormat must be CycloneDX")
    spec_version = document.get("specVersion")
    if spec_version not in SUPPORTED_CYCLONEDX_VERSIONS:
        raise EvidenceValidationError(f"{path}: unsupported CycloneDX specVersion {spec_version!r}")
    if document.get("version") != 1:
        raise EvidenceValidationError(f"{path}: CycloneDX document version must be 1")
    serial_number = document.get("serialNumber")
    if not isinstance(serial_number, str) or SERIAL_NUMBER.fullmatch(serial_number) is None:
        raise EvidenceValidationError(f"{path}: valid urn:uuid serialNumber is required")

    names = _component_names(document)
    normalized_expected = _normalized_name(expected_component)
    if normalized_expected not in names:
        raise EvidenceValidationError(
            f"{path}: expected component {expected_component!r} is missing"
        )
    if len(names) < 2:
        raise EvidenceValidationError(f"{path}: SBOM must contain the project and its dependencies")
    return {
        "component_count": len(names),
        "expected_component": normalized_expected,
        "spec_version": spec_version,
    }


def validate_grype_report(path: Path, *, expected_image: str) -> dict[str, object]:
    """Validate one Grype JSON report and return minimized severity evidence."""
    document = load_document(path)
    descriptor = document.get("descriptor")
    if not isinstance(descriptor, dict) or descriptor.get("name") != "grype":
        raise EvidenceValidationError(f"{path}: report descriptor must identify Grype")
    if descriptor.get("version") != EXPECTED_GRYPE_VERSION:
        raise EvidenceValidationError(
            f"{path}: Grype version must be {EXPECTED_GRYPE_VERSION}, "
            f"got {descriptor.get('version')!r}"
        )
    matches = document.get("matches")
    if not isinstance(matches, list):
        raise EvidenceValidationError(f"{path}: report matches must be an array")
    source = document.get("source")
    if not isinstance(source, dict) or source.get("type") != "image":
        raise EvidenceValidationError(f"{path}: report source must be a container image")
    target = source.get("target")
    if not isinstance(target, dict) or expected_image not in json.dumps(target, sort_keys=True):
        raise EvidenceValidationError(f"{path}: report target must identify {expected_image!r}")

    severities: Counter[str] = Counter()
    for match in matches:
        if not isinstance(match, dict):
            raise EvidenceValidationError(f"{path}: every vulnerability match must be an object")
        vulnerability = match.get("vulnerability")
        if isinstance(vulnerability, dict) and isinstance(vulnerability.get("severity"), str):
            severities[vulnerability["severity"].lower()] += 1
    return {
        "match_count": len(matches),
        "severities": dict(sorted(severities.items())),
        "tool_version": EXPECTED_GRYPE_VERSION,
    }


def parse_args() -> argparse.Namespace:
    """Parse evidence paths and expected identities."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sbom", type=Path, required=True)
    parser.add_argument("--image-sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--image", required=True)
    return parser.parse_args()


def main() -> int:
    """Validate all evidence files and print a metadata-only summary."""
    args = parse_args()
    try:
        result = {
            "image_sbom": validate_cyclonedx(
                args.image_sbom,
                expected_component=args.component,
            ),
            "source_sbom": validate_cyclonedx(
                args.source_sbom,
                expected_component=args.component,
            ),
            "vulnerabilities": validate_grype_report(
                args.vulnerability_report,
                expected_image=args.image,
            ),
        }
    except EvidenceValidationError as exc:
        print(f"Supply-chain evidence validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
