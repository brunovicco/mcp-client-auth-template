from pathlib import Path

from scripts.compose_reference_demo import _parser

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "compose.reference-demo.yml"
_OIDC_DOCKERFILE = _ROOT / "Dockerfile.reference-oidc"
_WRAPPER = _ROOT / "scripts/run_compose_demo.sh"
_SERVER_IMAGE = (
    "ghcr.io/brunovicco/mcp-server-auth-template@"
    "sha256:4a220992b5df2382b2f821713b8b4c840469e4465395cbdeb1349dee0f8a1110"
)


def test_compose_uses_immutable_server_subject() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")
    oidc_dockerfile = _OIDC_DOCKERFILE.read_text(encoding="utf-8")
    assert _SERVER_IMAGE in compose
    assert _SERVER_IMAGE in oidc_dockerfile


def test_compose_preserves_real_loopback_boundary() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    assert text.count('network_mode: "service:server"') == 2
    assert 'MCP_SERVER_RESOURCE_SERVER_URL: "http://127.0.0.1:8000/"' in text
    assert 'MCP_SERVER_GENERIC_ISSUER_URL: "http://127.0.0.1:9000"' in text
    assert "ports:" not in text


def test_compose_hardening() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "read_only: true" in text
    assert "cap_drop:" in text
    assert "- ALL" in text
    assert "no-new-privileges:true" in text
    assert "privileged:" not in text
    assert "/var/run/docker.sock" not in text


def test_wrapper_is_fail_closed_and_cleans_up() -> None:
    text = _WRAPPER.read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in text
    assert "@sha256:" in text
    assert "compose config --quiet" in text
    assert "compose pull server" in text
    assert "--exit-code-from demo" in text
    assert "compose down --volumes --remove-orphans" in text


def test_compose_runner_defaults_to_shared_loopback() -> None:
    args = _parser().parse_args([])
    assert args.server_url == "http://127.0.0.1:8000"
    assert args.issuer == "http://127.0.0.1:9000"
