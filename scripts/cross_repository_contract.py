"""Validate the versioned client/server interoperability contract."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import TypedDict, cast

CONTRACT_RELATIVE_PATH = Path("compatibility/cross-repository.json")
PROTOCOL_VERSION = "2026-07-28"
CLIENT_REPOSITORY = "brunovicco/mcp-client-auth-template"
SERVER_REPOSITORY = "brunovicco/mcp-server-auth-template"
_REQUIRED_POSITIVE_EVIDENCE = frozenset(
    {
        "protected-resource-metadata",
        "authorization-server-discovery",
        "dynamic-client-registration",
        "pkce-s256",
        "authorization-response-issuer",
        "resource-indicator",
        "bearer-access-token",
        "server-discover",
        "tools/call:whoami",
    }
)
_REQUIRED_NEGATIVE_EVIDENCE = frozenset(
    {
        "authorization-server-binding",
        "wrong-audience",
        "wrong-issuer",
        "expired-token",
        "insufficient-scope",
        "authorization-response-issuer-mismatch",
    }
)


class RepositoryPair(TypedDict):
    """Repositories participating in the compatibility contract."""

    client: str
    server: str


class CrossRepositoryContract(TypedDict):
    """Validated machine-readable cross-repository contract."""

    schema_version: int
    protocol_version: str
    transport: str
    auth_profile: str
    required_scope: str
    repositories: RepositoryPair
    positive_evidence: list[str]
    negative_evidence: list[str]


class CrossRepositoryContractError(RuntimeError):
    """Raised when local or peer compatibility evidence is invalid."""


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CrossRepositoryContractError(f"{field} must be a non-empty string")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise CrossRepositoryContractError(f"{field} must be a non-empty string list")
    items = cast(list[str], value)
    if len(items) != len(set(items)):
        raise CrossRepositoryContractError(f"{field} must not contain duplicates")
    return items


def load_contract(path: Path) -> CrossRepositoryContract:
    """Load and structurally validate one contract document."""
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossRepositoryContractError("unable to read compatibility contract") from exc
    if not isinstance(raw, dict):
        raise CrossRepositoryContractError("compatibility contract must be a JSON object")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise CrossRepositoryContractError("unsupported compatibility contract schema")

    repositories = raw.get("repositories")
    if not isinstance(repositories, dict):
        raise CrossRepositoryContractError("repositories must be a JSON object")
    pair: RepositoryPair = {
        "client": _string(repositories.get("client"), "repositories.client"),
        "server": _string(repositories.get("server"), "repositories.server"),
    }

    contract: CrossRepositoryContract = {
        "schema_version": schema_version,
        "protocol_version": _string(raw.get("protocol_version"), "protocol_version"),
        "transport": _string(raw.get("transport"), "transport"),
        "auth_profile": _string(raw.get("auth_profile"), "auth_profile"),
        "required_scope": _string(raw.get("required_scope"), "required_scope"),
        "repositories": pair,
        "positive_evidence": _string_list(raw.get("positive_evidence"), "positive_evidence"),
        "negative_evidence": _string_list(raw.get("negative_evidence"), "negative_evidence"),
    }
    return contract


def validate_contract(contract: CrossRepositoryContract) -> None:
    """Validate the semantic commitments encoded by the contract."""
    if contract["protocol_version"] != PROTOCOL_VERSION:
        raise CrossRepositoryContractError("unexpected MCP protocol version")
    if contract["transport"] != "streamable-http":
        raise CrossRepositoryContractError("unexpected MCP transport")
    if contract["auth_profile"] != "generic-oidc-oauth-2.1":
        raise CrossRepositoryContractError("unexpected authorization profile")
    if contract["required_scope"] != "mcp:tools:call":
        raise CrossRepositoryContractError("unexpected required scope")
    if contract["repositories"] != {
        "client": CLIENT_REPOSITORY,
        "server": SERVER_REPOSITORY,
    }:
        raise CrossRepositoryContractError("unexpected repository pair")
    if set(contract["positive_evidence"]) != _REQUIRED_POSITIVE_EVIDENCE:
        raise CrossRepositoryContractError("positive interoperability evidence is incomplete")
    if set(contract["negative_evidence"]) != _REQUIRED_NEGATIVE_EVIDENCE:
        raise CrossRepositoryContractError("negative interoperability evidence is incomplete")


def _canonical_bytes(contract: CrossRepositoryContract) -> bytes:
    return json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_pair(local_path: Path, peer_root: Path | None = None) -> dict[str, object]:
    """Validate the local contract and optionally require an identical peer contract."""
    local = load_contract(local_path)
    validate_contract(local)
    local_bytes = _canonical_bytes(local)

    peer_match = peer_root is not None
    if peer_root is not None:
        peer_path = peer_root / CONTRACT_RELATIVE_PATH
        peer = load_contract(peer_path)
        validate_contract(peer)
        if _canonical_bytes(peer) != local_bytes:
            raise CrossRepositoryContractError("peer compatibility contract does not match")

    return {
        "status": "ok",
        "protocol_version": local["protocol_version"],
        "transport": local["transport"],
        "auth_profile": local["auth_profile"],
        "peer_match": peer_match,
        "contract_sha256": hashlib.sha256(local_bytes).hexdigest(),
    }


def main() -> None:
    """Validate local and optional peer compatibility contracts."""
    parser = argparse.ArgumentParser(description="Validate cross-repository MCP compatibility")
    parser.add_argument(
        "--contract",
        type=Path,
        default=CONTRACT_RELATIVE_PATH,
        help="path to the local compatibility contract",
    )
    parser.add_argument(
        "--peer-root",
        type=Path,
        default=None,
        help="optional companion repository root containing the same contract",
    )
    args = parser.parse_args()
    payload = validate_pair(args.contract, args.peer_root)
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
