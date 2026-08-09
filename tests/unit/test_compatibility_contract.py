"""Tests for the executable Python/MCP SDK compatibility contract."""

from pathlib import Path

import pytest
from scripts.compatibility_contract import (
    MCP_MINIMUM_VERSION,
    CompatibilityContractError,
    validate_contract,
)


def _write_pyproject(path: Path, *, python: str = ">=3.13,<3.15", mcp: str = ">=2.0,<3") -> Path:
    target = path / "pyproject.toml"
    target.write_text(
        "\n".join(
            [
                "[project]",
                'name = "compatibility-fixture"',
                'version = "0.0.0"',
                f'requires-python = "{python}"',
                "dependencies = [",
                f'    "mcp{mcp}",',
                "]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return target


def test_minimum_profile_accepts_exact_support_floor(tmp_path: Path) -> None:
    result = validate_contract(
        pyproject_path=_write_pyproject(tmp_path),
        expected_python="3.13",
        mcp_profile="minimum",
        active_python="3.13",
        installed_mcp_version=MCP_MINIMUM_VERSION,
    )

    assert result == {
        "mcp_profile": "minimum",
        "mcp_version": "2.0.0",
        "python": "3.13",
        "status": "ok",
    }


def test_latest_profile_accepts_newer_2x_on_python_314(tmp_path: Path) -> None:
    result = validate_contract(
        pyproject_path=_write_pyproject(tmp_path),
        expected_python="3.14",
        mcp_profile="latest",
        active_python="3.14",
        installed_mcp_version="2.7.3",
    )

    assert result["status"] == "ok"
    assert result["mcp_version"] == "2.7.3"


@pytest.mark.parametrize(
    ("python_requirement", "mcp_requirement", "message"),
    [
        (">=3.12,<3.15", ">=2.0,<3", "requires-python drifted"),
        (">=3.13,<3.15", ">=2.1,<3", "MCP SDK requirement drifted"),
    ],
)
def test_metadata_drift_fails_closed(
    tmp_path: Path,
    python_requirement: str,
    mcp_requirement: str,
    message: str,
) -> None:
    with pytest.raises(CompatibilityContractError, match=message):
        validate_contract(
            pyproject_path=_write_pyproject(
                tmp_path,
                python=python_requirement,
                mcp=mcp_requirement,
            ),
            expected_python="3.13",
            mcp_profile="minimum",
            active_python="3.13",
            installed_mcp_version="2.0.0",
        )


def test_minimum_profile_rejects_version_above_floor(tmp_path: Path) -> None:
    with pytest.raises(CompatibilityContractError, match="support floor"):
        validate_contract(
            pyproject_path=_write_pyproject(tmp_path),
            expected_python="3.13",
            mcp_profile="minimum",
            active_python="3.13",
            installed_mcp_version="2.0.1",
        )


def test_latest_profile_rejects_next_major(tmp_path: Path) -> None:
    with pytest.raises(CompatibilityContractError, match=r"supported MCP 2\.x range"):
        validate_contract(
            pyproject_path=_write_pyproject(tmp_path),
            expected_python="3.14",
            mcp_profile="latest",
            active_python="3.14",
            installed_mcp_version="3.0.0",
        )


def test_active_interpreter_must_match_matrix_cell(tmp_path: Path) -> None:
    with pytest.raises(CompatibilityContractError, match="active Python interpreter"):
        validate_contract(
            pyproject_path=_write_pyproject(tmp_path),
            expected_python="3.14",
            mcp_profile="latest",
            active_python="3.13",
            installed_mcp_version="2.0.1",
        )
