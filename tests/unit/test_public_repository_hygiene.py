import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _git_paths(*args: str) -> set[str]:
    git = shutil.which("git")
    assert git is not None, "git executable is required for repository hygiene tests"

    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z", *args],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {item for item in result.stdout.split("\0") if item}


def test_local_agent_state_is_not_part_of_effective_tracked_tree() -> None:
    # `git ls-files` still reports index entries deleted from the working tree
    # before staging. Subtract them so this regression test works both before
    # staging and after the final commit.
    effective_tracked = _git_paths() - _git_paths("--deleted")

    forbidden_prefixes = (
        ".agent/",
        ".agents/",
        ".codex/",
        ".claude/",
        ".cursor/",
        ".aider/",
    )
    forbidden_files = {
        ".harness.json",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/MCP.md",
        "docs/mcp/project-config.example.toml",
        "scripts/validate_mcp_config.py",
    }

    assert not (effective_tracked & forbidden_files)
    assert not any(
        path.startswith(prefix) for path in effective_tracked for prefix in forbidden_prefixes
    )


def test_gitignore_keeps_local_agent_state_out() -> None:
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")

    for entry in (
        ".agent/",
        ".agents/",
        ".codex/",
        ".claude/",
        ".cursor/",
        ".aider/",
        ".harness.json",
        "AGENTS.md",
        "CLAUDE.md",
    ):
        assert entry in text
