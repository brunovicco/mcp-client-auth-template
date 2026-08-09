"""Validate the repository's executable Python/MCP SDK compatibility contract."""

import argparse
import json
import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

SUPPORTED_PYTHON_MINORS = ("3.13", "3.14")
REQUIRES_PYTHON = ">=3.13,<3.15"
MCP_MINIMUM_VERSION = "2.0.0"
MCP_SUPPORTED_RANGE = ">=2.0,<3"
_MCP_REQUIREMENT = f"mcp{MCP_SUPPORTED_RANGE}"
_RELEASE_PREFIX = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?")
_MCP_DEPENDENCY = re.compile(r"^mcp(?=[<>=!~;\[]|$)", re.IGNORECASE)


class CompatibilityContractError(RuntimeError):
    """Raised when package metadata or the active environment violates support policy."""


def _project_contract(pyproject_path: Path) -> tuple[str, str]:
    """Return the declared Python and MCP requirements from ``pyproject.toml``."""
    with pyproject_path.open("rb") as stream:
        project = tomllib.load(stream).get("project", {})

    requires_python = project.get("requires-python")
    dependencies = project.get("dependencies")
    if not isinstance(requires_python, str) or not isinstance(dependencies, list):
        raise CompatibilityContractError("project compatibility metadata is incomplete")

    mcp_requirements = [
        dependency.replace(" ", "")
        for dependency in dependencies
        if isinstance(dependency, str)
        and _MCP_DEPENDENCY.match(dependency.replace(" ", "")) is not None
    ]
    if len(mcp_requirements) != 1:
        raise CompatibilityContractError("project must declare exactly one MCP SDK requirement")
    return requires_python.replace(" ", ""), mcp_requirements[0]


def _release_triplet(raw_version: str) -> tuple[int, int, int]:
    """Parse the numeric release prefix needed by the compatibility policy."""
    match = _RELEASE_PREFIX.match(raw_version)
    if match is None:
        raise CompatibilityContractError("installed MCP SDK version is not parseable")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch") or 0),
    )


def validate_contract(
    *,
    pyproject_path: Path,
    expected_python: str,
    mcp_profile: str,
    active_python: str | None = None,
    installed_mcp_version: str | None = None,
) -> dict[str, str]:
    """Validate metadata, interpreter and installed MCP SDK against one matrix cell."""
    if expected_python not in SUPPORTED_PYTHON_MINORS:
        raise CompatibilityContractError("requested Python version is outside support policy")
    if mcp_profile not in {"minimum", "latest"}:
        raise CompatibilityContractError("unknown MCP compatibility profile")

    requires_python, mcp_requirement = _project_contract(pyproject_path)
    if requires_python != REQUIRES_PYTHON:
        raise CompatibilityContractError("requires-python drifted from compatibility policy")
    if mcp_requirement != _MCP_REQUIREMENT:
        raise CompatibilityContractError("MCP SDK requirement drifted from compatibility policy")

    current_python = active_python or f"{sys.version_info.major}.{sys.version_info.minor}"
    if current_python != expected_python:
        raise CompatibilityContractError("active Python interpreter does not match matrix cell")

    if installed_mcp_version is None:
        try:
            installed_mcp_version = version("mcp")
        except PackageNotFoundError as exc:
            raise CompatibilityContractError("MCP SDK is not installed") from exc

    release = _release_triplet(installed_mcp_version)
    if mcp_profile == "minimum":
        if installed_mcp_version != MCP_MINIMUM_VERSION:
            raise CompatibilityContractError("minimum profile did not install the support floor")
    elif release[0] != 2 or release < (2, 0, 0):
        raise CompatibilityContractError(
            "latest profile resolved outside the supported MCP 2.x range"
        )

    return {
        "mcp_profile": mcp_profile,
        "mcp_version": installed_mcp_version,
        "python": current_python,
        "status": "ok",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, dest="expected_python")
    parser.add_argument("--mcp-profile", required=True, choices=("minimum", "latest"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    return parser


def main() -> int:
    """Run the compatibility contract and emit a compact machine-readable result."""
    args = _parser().parse_args()
    try:
        payload = validate_contract(
            pyproject_path=args.pyproject,
            expected_python=args.expected_python,
            mcp_profile=args.mcp_profile,
        )
    except (CompatibilityContractError, OSError, tomllib.TOMLDecodeError) as exc:
        error = {"error": "compatibility_contract_failed", "reason": str(exc)}
        print(json.dumps(error, sort_keys=True), file=sys.stderr)
        return 1

    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
