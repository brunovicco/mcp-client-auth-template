#!/usr/bin/env python3
"""Validate the repository's dependency and GitHub Actions trust baseline."""

import re
import sys
import tomllib
from pathlib import Path

ACTION_REFERENCE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<reference>[^\s#]+)", re.MULTILINE)
FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
CONTAINER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PERMISSIONS_HEADER = re.compile(
    r"^(?P<indent> *)permissions:[ \t]*(?P<value>[^#\n]*)",
    re.MULTILINE,
)
PERMISSION_ENTRY = re.compile(r"^(?P<name>[a-z-]+):\s*(?P<access>read|write|none)\s*$")
JOB_HEADER = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*(?:#.*)?$")
RELEASE_WORKFLOW = Path(".github/workflows/release-artifacts.yml")
RELEASE_JOB_WRITE_PERMISSIONS = {
    "build-python-artifacts": {"artifact-metadata", "attestations", "id-token"},
    "publish-container": {"artifact-metadata", "attestations", "id-token", "packages"},
    "publish-github-release": {"contents"},
}

REQUIRED_FILES = (
    Path("build-constraints.txt"),
    Path(".github/dependabot.yml"),
    Path(".github/workflows/dependency-review.yml"),
    RELEASE_WORKFLOW,
    Path(".github/workflows/sbom.yml"),
    Path("docs/adr/0020-actionable-vulnerability-exceptions.md"),
    Path("docs/adr/0021-reproducible-release-provenance.md"),
    Path("docs/adr/0022-secure-release-publication.md"),
    Path("docs/SUPPLY_CHAIN.md"),
    Path("scripts/enforce_vulnerability_policy.py"),
    Path("scripts/install_security_tools.sh"),
    Path("scripts/prepare_release_artifacts.py"),
    Path("scripts/prepare_release_publication.py"),
    Path("scripts/validate_security_evidence.py"),
    Path("security/vulnerability-exceptions.json"),
)
REQUIRED_DENIED_LICENSES = (
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
)


def _display(path: Path, root: Path | None = None) -> str:
    """Return a stable repository-relative path when possible."""
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _permission_entries(lines: list[str], start: int, indent: int) -> dict[str, str]:
    """Read one YAML permissions mapping without accepting nested structures."""
    entries: dict[str, str] = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent <= indent:
            break
        match = PERMISSION_ENTRY.fullmatch(stripped.split(" #", maxsplit=1)[0])
        if match is not None:
            entries[match.group("name")] = match.group("access")
    return entries


def _checkout_discards_credentials(lines: list[str], uses_line: int) -> bool:
    """Return whether one checkout step explicitly disables credential persistence."""
    uses_indent = len(lines[uses_line]) - len(lines[uses_line].lstrip(" "))
    for line in lines[uses_line + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent < uses_indent:
            break
        if stripped.split(" #", maxsplit=1)[0] == "persist-credentials: false":
            return True
    return False


def _permission_job(lines: list[str], start: int, indent: int) -> str | None:
    """Return the job that owns a job-level permissions block."""
    if indent != 4:
        return None
    for line in reversed(lines[:start]):
        if line == "jobs:":
            break
        match = JOB_HEADER.fullmatch(line)
        if match is not None:
            return match.group("name")
    return None


def validate_workflow(path: Path, *, root: Path | None = None) -> list[str]:
    """Return trust-baseline violations for one GitHub Actions workflow."""
    display = _display(path, root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{display}: could not read workflow: {exc}"]

    errors: list[str] = []
    lines = text.splitlines()
    permission_blocks = list(PERMISSIONS_HEADER.finditer(text))
    top_level = next((block for block in permission_blocks if block.group("indent") == ""), None)
    if top_level is None:
        errors.append(f"{display}: top-level permissions mapping is required")
    elif top_level.group("value").strip():
        errors.append(f"{display}: top-level permissions must be an explicit mapping")
    else:
        line_number = text[: top_level.start()].count("\n")
        entries = _permission_entries(lines, line_number, 0)
        if entries.get("contents") != "read":
            errors.append(f"{display}: top-level permissions must include contents: read")

    for block in permission_blocks:
        value = block.group("value").strip()
        if value in {"write-all", "read-all"}:
            errors.append(f"{display}: aggregate permission {value!r} is not allowed")
            continue
        if value:
            errors.append(f"{display}: permissions must use an explicit mapping")
            continue
        line_number = text[: block.start()].count("\n")
        indent = len(block.group("indent"))
        entries = _permission_entries(lines, line_number, indent)
        job = _permission_job(lines, line_number, indent)
        allowed_write_permissions = (
            RELEASE_JOB_WRITE_PERMISSIONS.get(job, set())
            if display == RELEASE_WORKFLOW.as_posix() and job is not None
            else set()
        )
        for name, access in entries.items():
            if access == "write" and name not in allowed_write_permissions:
                errors.append(
                    f"{display}: write permission is outside the job allowlist "
                    f"({job or 'workflow'}: {name}: write)"
                )

    for match in ACTION_REFERENCE.finditer(text):
        reference = match.group("reference")
        line = text[: match.start()].count("\n") + 1
        if reference.startswith("./"):
            continue
        if reference.startswith("docker://"):
            _, separator, digest = reference.partition("@")
            if not separator or CONTAINER_DIGEST.fullmatch(digest) is None:
                errors.append(f"{display}:{line}: container action must be pinned by sha256 digest")
            continue
        action, separator, revision = reference.rpartition("@")
        if not separator or "/" not in action or FULL_COMMIT_SHA.fullmatch(revision) is None:
            errors.append(f"{display}:{line}: third-party action must use a full commit SHA")
        if action == "actions/checkout" and not _checkout_discards_credentials(lines, line - 1):
            errors.append(f"{display}:{line}: checkout must set persist-credentials: false")
    return errors


def _validate_baseline_configuration(root: Path) -> list[str]:
    """Validate required policy and automation configuration."""
    errors = [
        f"{path.as_posix()}: required supply-chain control file is missing"
        for path in REQUIRED_FILES
        if not (root / path).is_file()
    ]

    dependabot_path = root / ".github/dependabot.yml"
    if dependabot_path.is_file():
        text = dependabot_path.read_text(encoding="utf-8")
        for ecosystem in ('"uv"', '"github-actions"'):
            if f"package-ecosystem: {ecosystem}" not in text:
                errors.append(f".github/dependabot.yml: missing {ecosystem} update configuration")
        if text.count('interval: "weekly"') < 2:
            errors.append(".github/dependabot.yml: both ecosystems must use a weekly cadence")
        if text.count("open-pull-requests-limit:") < 2:
            errors.append(".github/dependabot.yml: each ecosystem needs an explicit PR limit")

    review_path = root / ".github/workflows/dependency-review.yml"
    if review_path.is_file():
        text = review_path.read_text(encoding="utf-8")
        if "pull_request:" not in text:
            errors.append("dependency-review.yml: workflow must run for pull requests")
        if "actions/dependency-review-action@" not in text:
            errors.append("dependency-review.yml: dependency review action is missing")
        if "fail-on-severity: high" not in text:
            errors.append("dependency-review.yml: vulnerability threshold must remain high")
        if "vulnerability-check: true" not in text:
            errors.append("dependency-review.yml: vulnerability review must remain enabled")
        if "license-check: true" not in text:
            errors.append("dependency-review.yml: license review must remain enabled")
        for license_id in REQUIRED_DENIED_LICENSES:
            if license_id not in text:
                errors.append(f"dependency-review.yml: denied license is missing: {license_id}")

    evidence_path = root / ".github/workflows/sbom.yml"
    if evidence_path.is_file():
        text = evidence_path.read_text(encoding="utf-8")
        required_evidence = {
            "source CycloneDX SBOM": "source.cdx.json",
            "image CycloneDX SBOM": "image.cdx.json",
            "complete vulnerability report": "grype.json",
            "evidence contract validation": "validate_security_evidence.py",
            "actionable vulnerability policy": "enforce_vulnerability_policy.py",
            "reviewed vulnerability exceptions": "vulnerability-exceptions.json",
            "policy decision evidence": "policy.json",
            "explicit source identity": "--source-name",
            "artifact upload": "actions/upload-artifact@",
        }
        for control, marker in required_evidence.items():
            if marker not in text:
                errors.append(f"sbom.yml: missing {control}")

    installer_path = root / "scripts/install_security_tools.sh"
    if installer_path.is_file():
        text = installer_path.read_text(encoding="utf-8")
        for marker in ('SYFT_VERSION="1.50.0"', 'GRYPE_VERSION="0.116.1"'):
            if marker not in text:
                errors.append(f"install_security_tools.sh: missing pinned marker {marker}")
        checksums = re.findall(r'checksum="([0-9a-f]{64})"', text)
        if len(checksums) != 8 or len(set(checksums)) != 8:
            errors.append("install_security_tools.sh: expected eight unique platform checksums")

    release_path = root / RELEASE_WORKFLOW
    if release_path.is_file():
        text = release_path.read_text(encoding="utf-8")
        required_release_controls = {
            "tag-only trigger": "tags:",
            "serialized tag publication": "cancel-in-progress: false",
            "commit-derived timestamp": "SOURCE_DATE_EPOCH",
            "pinned isolated build environment": "--build-constraints build-constraints.txt",
            "release contract validation": "prepare_release_artifacts.py",
            "package checksum publication": "SHA256SUMS",
            "checksum-bound attestation": "subject-checksums: dist/SHA256SUMS",
            "GitHub provenance": ("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"),
            "OIDC signing permission": "id-token: write",
            "attestation permission": "attestations: write",
            "artifact metadata permission": "artifact-metadata: write",
            "package SBOM attestation": "subject-checksums: build/python-artifacts/SHA256SUMS",
            "image digest subject": "subject-digest:",
            "registry attestation": "push-to-registry: true",
            "pre-publication vulnerability policy": "enforce_vulnerability_policy.py",
            "GHCR publication": "docker push",
            "published digest record": "image-digest.txt",
            "release assembly validation": "scripts.prepare_release_publication",
            "GitHub Release publication": "gh release create",
            "existing tag verification": "--verify-tag",
            "pinned artifact restore": (
                "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
            ),
            "release evidence retention": "retention-days: 30",
        }
        for control, marker in required_release_controls.items():
            if marker not in text:
                errors.append(f"release-artifacts.yml: missing {control}")
        if text.count("uv build --build-constraints") != 2:
            errors.append("release-artifacts.yml: exactly two reproducibility builds are required")
        if text.count("sbom-path:") != 2:
            errors.append("release-artifacts.yml: Python and image SBOM attestations are required")
        if text.count("push-to-registry: true") != 2:
            errors.append("release-artifacts.yml: both image attestations must be stored in GHCR")
        if text.count("create-storage-record: false") != 2:
            errors.append(
                "release-artifacts.yml: user-owned repositories must disable storage records"
            )
        if text.count("contents: write") != 1 or text.count("packages: write") != 1:
            errors.append(
                "release-artifacts.yml: release and registry writes must each exist in one job"
            )
        if "pull_request:" in text:
            errors.append("release-artifacts.yml: publication must not run for pull requests")
        if ":latest" in text:
            errors.append("release-artifacts.yml: mutable latest image tags are not allowed")
        policy = text.find("enforce_vulnerability_policy.py")
        login = text.find("docker login")
        push = text.find("docker push")
        if min(policy, login, push) < 0 or not policy < login < push:
            errors.append(
                "release-artifacts.yml: vulnerability policy must pass before registry login/push"
            )

    publication_path = root / "scripts/prepare_release_publication.py"
    if publication_path.is_file():
        text = publication_path.read_text(encoding="utf-8")
        required_publication_controls = {
            "complete release checksums": "RELEASE_SHA256SUMS",
            "machine-readable release manifest": "release-manifest.json",
            "source SBOM": "source.cdx.json",
            "image SBOM": "image.cdx.json",
            "complete vulnerability report": "grype.json",
            "vulnerability policy decision": "policy.json",
            "immutable image subject": "image-digest.txt",
        }
        for control, marker in required_publication_controls.items():
            if marker not in text:
                errors.append(f"prepare_release_publication.py: missing {control}")

    pyproject_path = root / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            with pyproject_path.open("rb") as handle:
                pyproject = tomllib.load(handle)
            project = pyproject["project"]
            name = project["name"]
            build_requires = pyproject["build-system"]["requires"]
            selection = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]
        except (KeyError, OSError, tomllib.TOMLDecodeError, TypeError) as exc:
            errors.append(f"pyproject.toml: invalid explicit sdist selection: {exc}")
        else:
            module = re.sub(r"[-.]+", "_", name).lower() if isinstance(name, str) else ""
            expected = {
                f"src/{module}",
                "CHANGELOG.md",
                "LICENSE",
                "README.pt-BR.md",
            }
            actual = (
                {item for item in selection if isinstance(item, str)}
                if isinstance(selection, list)
                else set()
            )
            if actual != expected:
                errors.append(
                    "pyproject.toml: sdist only-include must match the release content allowlist"
                )
            if build_requires != ["hatchling==1.31.0"]:
                errors.append("pyproject.toml: Hatchling build backend must remain exactly pinned")

    constraints_path = root / "build-constraints.txt"
    if constraints_path.is_file():
        constraints = {
            line
            for raw_line in constraints_path.read_text(encoding="utf-8").splitlines()
            if (line := raw_line.strip()) and not line.startswith("#")
        }
        expected_constraints = {
            "hatchling==1.31.0",
            "packaging==26.3",
            "pathspec==1.1.1",
            "pluggy==1.6.0",
            "trove-classifiers==2026.6.1.19",
        }
        if constraints != expected_constraints:
            errors.append("build-constraints.txt: isolated build environment must remain exact")
    return errors


def validate_repository(root: Path) -> list[str]:
    """Return every supply-chain trust-baseline violation in a repository."""
    root = root.resolve()
    errors = _validate_baseline_configuration(root)
    workflow_dir = root / ".github/workflows"
    workflows = sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")))
    if not workflows:
        errors.append(".github/workflows: no workflows found")
    for workflow in workflows:
        errors.extend(validate_workflow(workflow, root=root))
    return errors


def main() -> int:
    """Validate the current repository and return a process status."""
    root = Path(__file__).resolve().parents[1]
    errors = validate_repository(root)
    if errors:
        print("Supply-chain trust baseline failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Supply-chain trust baseline passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
