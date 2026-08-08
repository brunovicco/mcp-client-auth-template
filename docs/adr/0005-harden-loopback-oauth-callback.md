# ADR-0005: Harden the native OAuth loopback callback as a bounded exact-state listener

- Status: Accepted
- Date: 2026-08-08

## Context

RFC 8252 recommends loopback IP redirects for desktop native applications and explicitly
recommends IP literals instead of `localhost`. It also says clients should listen only on the
loopback interface and keep the network port open only while the authorization response is
expected.

A one-shot `HTTPServer.handle_request()` is too brittle for a browser-facing callback. Browsers,
extensions, security products, or unrelated local processes can hit `/favicon.ico`, the wrong
path, or a malformed query before the authorization server's real redirect arrives. Treating that
first request as terminal creates an easy local denial of service. Conversely, accepting the first
`code` without checking the authorization request's exact state would weaken the SDK's CSRF and
response-binding guarantees.

## Decision

1. Accept only loopback IP literals (`127.0.0.0/8` or IPv6 loopback); reject hostnames such as
   `localhost` and any non-loopback address before binding.
2. Render IPv6 redirect URIs with RFC-compliant brackets and require one exact configured path.
3. Wrap the SDK redirect handler. When `OAuthClientProvider` gives the application the browser
   authorization URL, capture its single non-empty `state` and verify that its `redirect_uri`
   exactly equals this listener's URI before opening the browser.
4. Keep listening after invalid requests. Wrong path, favicon, malformed query encoding,
   duplicate parameters, wrong Host, wrong state, mixed `code` + `error`, and empty issuer do not
   consume the legitimate callback.
5. A callback is terminal only when it carries the exact expected state plus exactly one of a
   non-empty `code` or `error`. The SDK still performs its own state and RFC 9207 issuer checks.
6. Bound the listener with both a monotonic global deadline and a maximum number of handled
   requests. Invalid traffic cannot reset the timeout indefinitely.
7. Suppress access logs and return generic error bodies so authorization codes, state values, and
   issuer data are not emitted to logs or reflected in responses.

## Consequences

- Accidental browser probes no longer break the OAuth flow.
- Local requests with a guessed or stale state are rejected without terminating the real flow.
- Consumers using `LoopbackCallbackServer` directly must pass
  `loopback.wrap_redirect_handler(real_redirect_handler)` to `OAuthClientProvider`; calling
  `wait_for_callback()` before the listener has been armed fails closed.
- Broader outbound discovery SSRF controls remain P1.1e and token-file durability/permissions
  remain P1.1g; this ADR is intentionally limited to the inbound native callback surface.
