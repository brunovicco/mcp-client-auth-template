"""Client security audit records are deliberately minimal and secret-free."""

import pytest

from mcp_client_auth_template.adapters import security_audit
from mcp_client_auth_template.adapters.security_audit import (
    ClientSecurityAuditAction,
    ClientSecurityAuditOutcome,
    emit_client_security_audit,
)


class _CapturedLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def test_client_audit_event_has_no_credential_or_url_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _CapturedLogger()
    monkeypatch.setattr(security_audit, "logger", captured)

    emit_client_security_audit(
        ClientSecurityAuditAction.OUTBOUND_BEARER_BLOCKED,
        ClientSecurityAuditOutcome.DENIED,
        reason="bearer_target_not_mcp_resource",
        target_kind="oauth_or_unexpected_origin",
    )

    assert captured.events == [
        (
            "security_audit",
            {
                "schema_version": 1,
                "action": "outbound_bearer_blocked",
                "outcome": "denied",
                "reason": "bearer_target_not_mcp_resource",
                "target_kind": "oauth_or_unexpected_origin",
            },
        )
    ]
