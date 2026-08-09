"""Tests for the executable supply-chain trust baseline."""

from pathlib import Path

from scripts.validate_supply_chain import validate_repository, validate_workflow

_ROOT = Path(__file__).resolve().parents[2]


def _workflow(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "workflow.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_repository_supply_chain_baseline_is_valid() -> None:
    assert validate_repository(_ROOT) == []


def test_unpinned_third_party_action_is_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        """name: unsafe
on: pull_request
permissions:
  contents: read
jobs:
  test:
    steps:
      - uses: actions/checkout@v7
""",
    )

    errors = validate_workflow(path)

    assert any("full commit SHA" in error for error in errors)


def test_checkout_must_discard_persisted_credentials(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        """name: unsafe
on: pull_request
permissions:
  contents: read
jobs:
  test:
    steps:
      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
""",
    )

    errors = validate_workflow(path)

    assert any("persist-credentials: false" in error for error in errors)


def test_top_level_permissions_are_required(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        """name: unsafe
on: pull_request
jobs:
  test:
    steps: []
""",
    )

    errors = validate_workflow(path)

    assert any("top-level permissions mapping" in error for error in errors)


def test_write_permissions_are_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        """name: unsafe
on: pull_request
permissions:
  contents: write
jobs:
  test:
    steps: []
""",
    )

    errors = validate_workflow(path)

    assert any("contents: read" in error for error in errors)
    assert any("contents: write" in error for error in errors)


def test_local_actions_are_allowed_without_remote_revision(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        """name: local
on: pull_request
permissions:
  contents: read
jobs:
  test:
    steps:
      - uses: ./actions/check
""",
    )

    assert validate_workflow(path) == []
