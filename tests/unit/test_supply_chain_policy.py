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


def test_release_workflow_enforces_job_specific_write_permissions(tmp_path: Path) -> None:
    path = tmp_path / ".github/workflows/release-artifacts.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        """name: release
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
jobs:
  build-python-artifacts:
    permissions:
      contents: read
      id-token: write
      attestations: write
      artifact-metadata: write
    steps: []
  publish-container:
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
      artifact-metadata: write
    steps: []
  publish-github-release:
    permissions:
      contents: write
    steps: []
""",
        encoding="utf-8",
    )

    assert validate_workflow(path, root=tmp_path) == []


def test_release_workflow_rejects_registry_write_in_build_job(tmp_path: Path) -> None:
    path = tmp_path / ".github/workflows/release-artifacts.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        """name: release
on: push
permissions:
  contents: read
jobs:
  build-python-artifacts:
    permissions:
      contents: read
      packages: write
    steps: []
""",
        encoding="utf-8",
    )

    errors = validate_workflow(path, root=tmp_path)

    assert any("build-python-artifacts: packages: write" in error for error in errors)


def test_release_workflow_rejects_release_write_in_container_job(tmp_path: Path) -> None:
    path = tmp_path / ".github/workflows/release-artifacts.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        """name: release
on: push
permissions:
  contents: read
jobs:
  publish-container:
    permissions:
      contents: write
      packages: write
    steps: []
""",
        encoding="utf-8",
    )

    errors = validate_workflow(path, root=tmp_path)

    assert any("publish-container: contents: write" in error for error in errors)


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
