"""Tests for generated SBOM and vulnerability evidence validation."""

import json
from pathlib import Path

import pytest
from scripts.validate_security_evidence import (
    EXPECTED_GRYPE_VERSION,
    EvidenceValidationError,
    validate_cyclonedx,
    validate_grype_report,
)


def _write(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _sbom(component: str = "mcp-client-auth-template") -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:12345678-1234-4123-8123-123456789abc",
        "version": 1,
        "components": [
            {"name": component, "version": "0.4.0", "type": "application"},
            {"name": "pydantic", "version": "2.11.0", "type": "library"},
        ],
    }


def _report(image: str = "mcp-client-auth-template:ci") -> dict[str, object]:
    return {
        "descriptor": {"name": "grype", "version": EXPECTED_GRYPE_VERSION},
        "matches": [
            {
                "vulnerability": {"id": "CVE-TEST", "severity": "Low"},
                "artifact": {"name": "example", "version": "1.0"},
            }
        ],
        "source": {"type": "image", "target": {"userInput": image, "tags": [image]}},
    }


def test_valid_cyclonedx_evidence_returns_component_summary(tmp_path: Path) -> None:
    result = validate_cyclonedx(
        _write(tmp_path / "sbom.json", _sbom()),
        expected_component="mcp_client_auth_template",
    )

    assert result["component_count"] == 2
    assert result["spec_version"] == "1.6"


def test_cyclonedx_evidence_rejects_missing_project_component(tmp_path: Path) -> None:
    with pytest.raises(EvidenceValidationError, match="expected component"):
        validate_cyclonedx(
            _write(tmp_path / "sbom.json", _sbom(component="another-project")),
            expected_component="mcp-client-auth-template",
        )


def test_cyclonedx_evidence_rejects_wrong_format(tmp_path: Path) -> None:
    document = _sbom()
    document["bomFormat"] = "SPDX"

    with pytest.raises(EvidenceValidationError, match="bomFormat"):
        validate_cyclonedx(
            _write(tmp_path / "sbom.json", document),
            expected_component="mcp-client-auth-template",
        )


def test_valid_grype_report_returns_severity_summary(tmp_path: Path) -> None:
    result = validate_grype_report(
        _write(tmp_path / "grype.json", _report()),
        expected_image="mcp-client-auth-template:ci",
    )

    assert result["match_count"] == 1
    assert result["severities"] == {"low": 1}


def test_grype_report_rejects_tool_version_drift(tmp_path: Path) -> None:
    document = _report()
    descriptor = document["descriptor"]
    assert isinstance(descriptor, dict)
    descriptor["version"] = "0.0.0"

    with pytest.raises(EvidenceValidationError, match="Grype version"):
        validate_grype_report(
            _write(tmp_path / "grype.json", document),
            expected_image="mcp-client-auth-template:ci",
        )


def test_grype_report_rejects_wrong_image(tmp_path: Path) -> None:
    with pytest.raises(EvidenceValidationError, match="report target"):
        validate_grype_report(
            _write(tmp_path / "grype.json", _report(image="other:ci")),
            expected_image="mcp-client-auth-template:ci",
        )
