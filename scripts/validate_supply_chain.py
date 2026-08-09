#!/usr/bin/env python3
"""Validate the repository's dependency and GitHub Actions trust baseline."""

import re
import sys
from pathlib import Path

ACTION_REFERENCE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<reference>[^\s#]+)", re.MULTILINE)
FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
CONTAINER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PERMISSIONS_HEADER = re.compile(
    r"^(?P<indent> *)permissions:[ \t]*(?P<value>[^#\n]*)",
    re.MULTILINE,
)
PERMISSION_ENTRY = re.compile(r"^(?P<name>[a-z-]+):\s*(?P<access>read|write|none)\s*$")

REQUIRED_FILES = (
    Path(".github/dependabot.yml"),
    Path(".github/workflows/dependency-review.yml"),
    Path("docs/SUPPLY_CHAIN.md"),
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
        entries = _permission_entries(lines, line_number, len(block.group("indent")))
        for name, access in entries.items():
            if access == "write":
                errors.append(
                    f"{display}: write permission is not allowed in P1.6a ({name}: write)"
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
        f"{path.as_posix()}: required P1.6a baseline file is missing"
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
