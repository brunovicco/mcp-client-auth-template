# ADR-0017: Make runtime scope step-up executable without generic tool retries

- Status: Accepted
- Date: 2026-08-09

## Context

The client already delegates `403 insufficient_scope` handling to the official MCP Python SDK,
which merges previously requested or granted scopes with the current challenge, reauthorizes, and
replays the HTTP request once. The companion server already emits the required pre-dispatch
challenge, but the pair E2E proved only its shape with a manually minted under-scoped token.

ADR-0010 prohibits generic tool retries because a timeout or transport failure does not prove that
a side-effecting handler did not run. OAuth step-up is a narrower case: the resource server returns
the 403 during bearer verification, before MCP dispatch, so the original operation has not executed.

## Decision

- Use the companion server's elevated MCP `health` profile to exercise runtime step-up. The initial
  grant remains `mcp:tools:call`; the operation challenge adds `mcp:tools:health`.
- Keep scope parsing, union, reauthorization, token persistence, and the single request replay in
  the official SDK's `OAuthClientProvider`. Do not add an application-owned retry loop or call
  `call_tool_with_budget` a second time.
- Extend the fake authorization server's test state with the scopes presented at each authorization
  request.
- Add a live E2E proving the initial grant, one challenge, the ordered union, exactly two
  authorization/token exchanges, the elevated stored token, and successful completion of
  `health`.
- Add runtime step-up, scope-union reauthorization, and pre-dispatch replay to the shared pair
  contract.

## Consequences

- The normal demo path now illustrates incremental consent with the SDK support floor rather than
  claiming step-up from isolated unit behavior.
- The OAuth replay is an explicit exception to the no-generic-tool-retry decision: it is bounded by
  the SDK auth flow and is safe only because the 403 proves pre-dispatch rejection.
- Authorization-server deployments must expose the elevated health scope. Entra deployments use
  the companion server's Application ID URI-qualified delegated scope.
- The E2E remains local and deterministic and does not weaken token audience, issuer, PKCE, or
  authorization-server binding checks.
- Merge the companion server change before this client change because CI compares with
  `server/main`.
