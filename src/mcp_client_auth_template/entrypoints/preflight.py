"""Network-silent startup preflight for the interactive MCP client."""

import argparse
import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import ValidationError

from mcp_client_auth_template.entrypoints.settings import Settings

_ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "production"})
_PLACEHOLDER_SUFFIXES = (
    ".invalid",
    ".test",
    ".example.com",
    ".example.net",
    ".example.org",
)
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


class ConfigurationPreflightError(RuntimeError):
    """Raised when local client configuration is not safe to start."""


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    """One sanitized configuration problem safe to print in CI logs."""

    location: str
    type: str


def _environment() -> str:
    value = os.environ.get("APP_ENV", "development").strip().lower()
    if value not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("APP_ENV must be development, test, or production")
    return value


def _placeholder_host(value: str) -> bool:
    host = urlsplit(value).hostname
    if host is None:
        return True
    lowered = host.lower()
    return lowered == "localhost" or lowered.endswith(_PLACEHOLDER_SUFFIXES)


def validate_production_settings(settings: Settings, environment: str) -> list[PreflightIssue]:
    """Return production-only policy violations without performing network I/O."""
    if environment != "production":
        return []

    issues: list[PreflightIssue] = []
    if urlsplit(settings.server_url).scheme != "https":
        issues.append(PreflightIssue("server_url", "https_required_in_production"))
    if _placeholder_host(settings.server_url):
        issues.append(PreflightIssue("server_url", "placeholder_host_not_allowed"))
    if settings.oauth_allow_insecure_loopback:
        issues.append(
            PreflightIssue("oauth_allow_insecure_loopback", "insecure_loopback_not_allowed")
        )
    if settings.auth_provider == "entra":
        if settings.entra_tenant_id == _ZERO_UUID:
            issues.append(PreflightIssue("entra_tenant_id", "placeholder_identifier_not_allowed"))
        if settings.entra_client_id == _ZERO_UUID:
            issues.append(PreflightIssue("entra_client_id", "placeholder_identifier_not_allowed"))
    if settings.generic_client_metadata_url is not None and _placeholder_host(
        settings.generic_client_metadata_url
    ):
        issues.append(PreflightIssue("generic_client_metadata_url", "placeholder_host_not_allowed"))
    return issues


def run_preflight() -> tuple[Settings | None, str | None, list[PreflightIssue]]:
    """Load and validate local configuration without DNS, HTTP, browser, or token I/O."""
    try:
        environment = _environment()
    except ValueError:
        return None, None, [PreflightIssue("APP_ENV", "invalid_environment")]

    try:
        settings = Settings()
    except ValidationError as exc:
        issues = [
            PreflightIssue(
                ".".join(str(part) for part in error["loc"]) or "settings",
                str(error["type"]),
            )
            for error in exc.errors(include_input=False, include_url=False)
        ]
        return None, environment, issues

    return settings, environment, validate_production_settings(settings, environment)


def load_validated_settings() -> Settings:
    """Return settings only after the same preflight enforced by the CLI."""
    settings, _, issues = run_preflight()
    if issues:
        detail = ", ".join(f"{issue.location}:{issue.type}" for issue in issues)
        raise ConfigurationPreflightError(f"configuration preflight failed ({detail})")
    if settings is None:
        raise ConfigurationPreflightError("configuration preflight failed (settings:unavailable)")
    return settings


def _result_payload(
    settings: Settings | None,
    environment: str | None,
    issues: list[PreflightIssue],
) -> dict[str, object]:
    if issues:
        return {
            "status": "error",
            "error": "configuration_invalid",
            "issues": [{"location": issue.location, "type": issue.type} for issue in issues],
        }
    if settings is None or environment is None:
        return {
            "status": "error",
            "error": "configuration_invalid",
            "issues": [{"location": "preflight", "type": "invalid_success_state"}],
        }
    return {
        "status": "ok",
        "environment": environment,
        "auth_provider": settings.auth_provider,
        "token_storage": "memory" if settings.token_storage_path is None else "file",
    }


def main() -> None:
    """Validate configuration and exit non-zero without exposing configured values."""
    parser = argparse.ArgumentParser(description="Validate MCP client configuration without I/O")
    parser.add_argument("--json", action="store_true", help="emit one compact JSON object")
    args = parser.parse_args()

    settings, environment, issues = run_preflight()
    payload = _result_payload(settings, environment, issues)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    elif issues:
        print("configuration preflight failed")
        for issue in issues:
            print(f"- {issue.location}: {issue.type}")
    else:
        print("configuration preflight passed")
    raise SystemExit(0 if payload["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
