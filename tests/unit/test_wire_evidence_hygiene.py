"""Guard: real-wire acceptance suites never fabricate MCP result objects.

The companion server's ``tools/list`` bug went unnoticed because unit tests built typed SDK
results by hand while the real SDK handed over wire dicts. Acceptance evidence in
``tests/e2e`` and ``tests/integration`` must come from the SDK and the wire instead.
"""

import ast
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_ACCEPTANCE_SUITES = ("e2e", "integration")
_FABRICATED_RESULTS = frozenset(
    {
        "CallToolResult",
        "ListToolsResult",
        "Tool",
        "TextContent",
        "InitializeResult",
        "DiscoverResult",
    }
)


def _acceptance_files() -> list[Path]:
    return sorted(
        path for suite in _ACCEPTANCE_SUITES for path in (_TESTS_ROOT / suite).rglob("*.py")
    )


def _constructed_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            ("mcp", "mcp_types")
        ):
            names.update(alias.name for alias in node.names)
    return names


def test_acceptance_suites_exist() -> None:
    assert _acceptance_files()


@pytest.mark.parametrize("path", _acceptance_files(), ids=lambda path: path.name)
def test_acceptance_suite_does_not_build_mcp_results_by_hand(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert not _constructed_names(tree) & _FABRICATED_RESULTS
