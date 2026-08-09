"""Tests for the versioned cross-repository compatibility contract."""

import json
from pathlib import Path

import pytest
from scripts.cross_repository_contract import (
    CONTRACT_RELATIVE_PATH,
    CrossRepositoryContractError,
    load_contract,
    validate_contract,
    validate_pair,
)

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT = _ROOT / CONTRACT_RELATIVE_PATH


def _write_contract(tmp_path: Path, *, update: dict[str, object] | None = None) -> Path:
    payload = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    if update:
        payload.update(update)
    path = tmp_path / "cross-repository.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_repository_contract_is_valid() -> None:
    contract = load_contract(_CONTRACT)
    validate_contract(contract)


def test_pair_validation_without_peer_is_local_only() -> None:
    evidence = validate_pair(_CONTRACT)
    assert evidence["status"] == "ok"
    assert evidence["protocol_version"] == "2026-07-28"
    assert evidence["peer_match"] is False


def test_matching_peer_contract_is_accepted(tmp_path: Path) -> None:
    peer_root = tmp_path / "peer"
    peer_contract = peer_root / CONTRACT_RELATIVE_PATH
    peer_contract.parent.mkdir(parents=True)
    peer_contract.write_bytes(_CONTRACT.read_bytes())

    evidence = validate_pair(_CONTRACT, peer_root)

    assert evidence["peer_match"] is True


def test_protocol_version_drift_is_rejected(tmp_path: Path) -> None:
    path = _write_contract(tmp_path, update={"protocol_version": "2025-11-25"})
    with pytest.raises(CrossRepositoryContractError, match="unexpected MCP protocol version"):
        validate_contract(load_contract(path))


def test_repository_pair_drift_is_rejected(tmp_path: Path) -> None:
    path = _write_contract(
        tmp_path,
        update={
            "repositories": {
                "client": "example/client",
                "server": "example/server",
            }
        },
    )
    with pytest.raises(CrossRepositoryContractError, match="unexpected repository pair"):
        validate_contract(load_contract(path))


def test_missing_positive_evidence_is_rejected(tmp_path: Path) -> None:
    original = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(original, dict)
    evidence = original["positive_evidence"]
    assert isinstance(evidence, list)
    path = _write_contract(tmp_path, update={"positive_evidence": evidence[:-1]})
    with pytest.raises(CrossRepositoryContractError, match="positive interoperability evidence"):
        validate_contract(load_contract(path))


def test_peer_contract_drift_is_rejected(tmp_path: Path) -> None:
    peer_root = tmp_path / "peer"
    peer_contract = peer_root / CONTRACT_RELATIVE_PATH
    peer_contract.parent.mkdir(parents=True)
    drifted = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(drifted, dict)
    drifted["negative_evidence"] = ["wrong-audience"]
    peer_contract.write_text(json.dumps(drifted), encoding="utf-8")

    with pytest.raises(CrossRepositoryContractError, match="negative interoperability evidence"):
        validate_pair(_CONTRACT, peer_root)
