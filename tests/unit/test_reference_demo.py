from pathlib import Path

import pytest
from scripts.reference_demo import DemoError, _modern_tool_request, _resolve_server_root


def _make_server_root(root: Path) -> Path:
    (root / "src/mcp_server_auth_template").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "mcp-server-auth-template"\nversion = "0.5.0"\n',
        encoding="utf-8",
    )
    return root


def test_resolve_server_root_accepts_explicit_checkout(tmp_path: Path) -> None:
    server_root = _make_server_root(tmp_path / "server")

    resolved = _resolve_server_root(server_root)

    assert resolved == server_root.resolve()


def test_resolve_server_root_discovers_sibling_checkout(tmp_path: Path) -> None:
    client_root = tmp_path / "mcp-client-auth-template"
    client_root.mkdir()
    server_root = _make_server_root(tmp_path / "mcp-server-auth-template")

    resolved = _resolve_server_root(None, client_root=client_root)

    assert resolved == server_root.resolve()


def test_resolve_server_root_fails_closed_for_missing_checkout(tmp_path: Path) -> None:
    with pytest.raises(DemoError, match="companion server checkout not found"):
        _resolve_server_root(tmp_path / "missing")


def test_modern_tool_request_is_self_describing() -> None:
    request = _modern_tool_request()

    assert request["method"] == "tools/call"
    params = request["params"]
    assert isinstance(params, dict)
    assert params["name"] == "whoami"
    metadata = params["_meta"]
    assert isinstance(metadata, dict)
    assert metadata["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert metadata["io.modelcontextprotocol/clientCapabilities"] == {}
