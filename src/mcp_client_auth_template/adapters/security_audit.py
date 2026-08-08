"""Minimal, secret-free security audit events for the MCP client."""

from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)


class ClientSecurityAuditAction(StrEnum):
    """Stable client-side security event actions."""

    OUTBOUND_BEARER_BLOCKED = "outbound_bearer_blocked"


class ClientSecurityAuditOutcome(StrEnum):
    """Stable client-side security event outcomes."""

    DENIED = "denied"


def emit_client_security_audit(
    action: ClientSecurityAuditAction,
    outcome: ClientSecurityAuditOutcome,
    *,
    reason: str,
    target_kind: str,
) -> None:
    """Emit an allowlisted event without URL query data, headers, or credentials."""
    logger.info(
        "security_audit",
        schema_version=1,
        action=action.value,
        outcome=outcome.value,
        reason=reason,
        target_kind=target_kind,
    )
