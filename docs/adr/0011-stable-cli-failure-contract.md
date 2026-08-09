# ADR-0011: Use stable exit codes and secret-free failure telemetry

## Status

Accepted.

## Context

An interactive OAuth client has several expected failure domains: configuration, local token
storage, authorization, outbound network policy, HTTP transport, MCP deadlines, tool-level errors,
and MCP protocol errors. Letting every exception escape produces unstable process behavior and may
copy server-controlled or OAuth-sensitive exception text into logs or crash reports.

The demo also handled two data-bearing paths too casually for a production-oriented reference: a
failed system-browser launch attached the full authorization URL to a structured log event, and
successful demo calls logged their complete structured tool result.

## Decision

The CLI classifies expected operational exceptions into a small stable taxonomy with dedicated exit
codes. Failure telemetry is allowlisted to category, exit code, and exception class name. Exception
messages, OAuth URLs, token responses, MCP error data, and tool result content are not logged by the
entrypoint.

`CallToolResult.is_error` is treated as a failed CLI operation and mapped separately from an MCP
protocol error. The result payload is not copied into the raised error. `KeyboardInterrupt` maps to
exit 130; cancellation exceptions are not swallowed by the ordinary `Exception` boundary.

The authorization URL remains visible on the direct terminal output used for the headless manual
flow, but it is removed from structured logging. Successful demo tool calls log completion and the
safe tool name only.

## Consequences

Shell scripts and CI can react to failures without parsing exception strings. Operational logs have
bounded cardinality and do not become an accidental data-exfiltration channel. Debugging unknown
software defects requires a deliberate development/debug path rather than enabling unsafe exception
text in production logs.
