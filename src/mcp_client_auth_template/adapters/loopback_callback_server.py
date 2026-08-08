"""Hardened loopback callback server for native/CLI OAuth clients (RFC 8252 §7.3).

The listener binds only to a loopback IP literal, accepts only the configured callback
path, validates the exact OAuth ``state`` captured from the SDK-generated authorization
URL, and keeps listening after unrelated or malformed browser requests. A global deadline
and bounded request count prevent an invalid-request stream from extending the callback
window indefinitely.
"""

import ipaddress
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import anyio
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult

_SUCCESS_BODY = b"<html><body><h1>Authorization complete</h1>You can close this tab.</body></html>"
_ERROR_BODY = b"<html><body><h1>Authorization failed</h1>You can close this tab.</body></html>"
_INVALID_BODY = b"<html><body><h1>Invalid callback request</h1></body></html>"
_MAX_QUERY_FIELDS = 16
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _normalize_loopback_host(
    host: str,
) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return a canonical loopback IP literal and parsed address, rejecting hostnames."""
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise ValueError("loopback callback host must be an IP literal") from exc
    if not address.is_loopback:
        raise ValueError("loopback callback host must resolve to a loopback address")
    return address.compressed, address


def _validate_callback_path(path: str) -> str:
    """Validate one absolute path with no authority, query, fragment, or control bytes."""
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "?" in path
        or "#" in path
        or "\\" in path
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in path)
    ):
        raise ValueError("loopback callback path must be one absolute URL path")
    return path


def _parse_single_value_query(raw_query: str) -> dict[str, str] | None:
    """Parse a bounded query, rejecting malformed percent escapes and duplicate parameters."""
    if not raw_query or _INVALID_PERCENT_ESCAPE.search(raw_query):
        return None
    try:
        parsed = parse_qs(
            raw_query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=_MAX_QUERY_FIELDS,
        )
    except ValueError:
        return None
    if any(len(values) != 1 for values in parsed.values()):
        return None
    return {key: values[0] for key, values in parsed.items()}


class LoopbackCallbackServer:
    """Receive one validated OAuth response on a bounded loopback-only HTTP listener."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/callback",
        timeout_seconds: float = 300,
        max_requests: int = 32,
    ) -> None:
        """Validate immutable listener configuration; bind only in ``wait_for_callback``."""
        normalized_host, host_address = _normalize_loopback_host(host)
        if not 1 <= port <= 65535:
            raise ValueError("loopback callback port must be between 1 and 65535")
        if timeout_seconds <= 0:
            raise ValueError("loopback callback timeout_seconds must be positive")
        if max_requests <= 0:
            raise ValueError("loopback callback max_requests must be positive")

        self._host = normalized_host
        self._host_address = host_address
        self._port = port
        self._path = _validate_callback_path(path)
        self._timeout_seconds = timeout_seconds
        self._max_requests = max_requests
        self._expected_state: str | None = None

    @property
    def redirect_uri(self) -> str:
        """Return the exact RFC 8252 loopback URI registered with the authorization server."""
        authority = f"[{self._host}]" if self._host_address.version == 6 else self._host
        return f"http://{authority}:{self._port}{self._path}"

    def wrap_redirect_handler(
        self, handler: Callable[[str], Awaitable[None]]
    ) -> Callable[[str], Awaitable[None]]:
        """Capture the SDK-generated ``state`` before delegating to the browser opener.

        ``OAuthClientProvider`` generates ``state`` internally and exposes it only in the
        authorization URL handed to ``redirect_handler``. Capturing it here lets the loopback
        listener reject wrong-state requests *without* consuming the legitimate callback; the
        SDK still performs its own constant-time state comparison afterwards as defense in depth.
        """

        async def guarded_redirect(authorization_url: str) -> None:
            try:
                parsed = urlsplit(authorization_url)
            except ValueError as exc:
                raise OAuthFlowError("authorization URL was malformed") from exc
            query = _parse_single_value_query(parsed.query)
            if query is None or not query.get("state"):
                raise OAuthFlowError("authorization URL did not contain one valid state parameter")
            if query.get("redirect_uri") != self.redirect_uri:
                raise OAuthFlowError(
                    "authorization URL redirect_uri did not match the configured loopback listener"
                )

            self._expected_state = query["state"]
            try:
                await handler(authorization_url)
            except BaseException:
                self._expected_state = None
                raise

        return guarded_redirect

    def _host_header_matches(self, value: str | None) -> bool:
        """Return whether an HTTP Host header names exactly this loopback listener."""
        if not value:
            return False
        try:
            parsed = urlsplit(f"//{value}")
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.hostname is None
            ):
                return False
            address = ipaddress.ip_address(parsed.hostname)
            port = parsed.port or 80
        except ValueError:
            return False
        return address == self._host_address and port == self._port

    async def wait_for_callback(self) -> AuthorizationCodeResult:
        """Wait for one exact-state OAuth response within the fixed request/time budget.

        Unrelated paths, favicon probes, malformed queries, duplicate parameters, wrong Host
        headers, and wrong-state callbacks receive an HTTP error but do not terminate the OAuth
        flow. A syntactically valid authorization-server ``error`` response with the expected
        state is terminal and is surfaced as ``OAuthFlowError``.
        """
        expected_state = self._expected_state
        if expected_state is None:
            raise OAuthFlowError(
                "loopback callback listener was not armed; use wrap_redirect_handler()"
            )
        expected_state_value: str = expected_state

        captured: dict[str, str] | None = None
        expected_path = self._path
        callback_server = self

        class _Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, body: bytes = b"") -> None:
                self.send_response(status)
                if body:
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self) -> None:
                nonlocal captured
                try:
                    parsed = urlsplit(self.path)
                except ValueError:
                    self._send(400, _INVALID_BODY)
                    return

                if parsed.path == "/favicon.ico":
                    self._send(204)
                    return
                if parsed.path != expected_path:
                    self._send(404, _INVALID_BODY)
                    return
                if parsed.fragment or not callback_server._host_header_matches(
                    self.headers.get("Host")
                ):
                    self._send(400, _INVALID_BODY)
                    return

                query = _parse_single_value_query(parsed.query)
                if query is None:
                    self._send(400, _INVALID_BODY)
                    return

                state = query.get("state")
                if state is None:
                    self._send(400, _INVALID_BODY)
                    return
                if not secrets.compare_digest(state, expected_state_value):
                    self._send(400, _INVALID_BODY)
                    return

                code = query.get("code")
                error = query.get("error")
                if bool(code) == bool(error):
                    self._send(400, _INVALID_BODY)
                    return
                if query.get("iss") == "":
                    self._send(400, _INVALID_BODY)
                    return

                captured = query
                if error:
                    self._send(400, _ERROR_BODY)
                else:
                    self._send(200, _SUCCESS_BODY)

            def log_message(self, format_str: str, *args: object) -> None:
                """Suppress access logs so codes, state and issuer never reach stderr."""

        server_type = HTTPServer
        if self._host_address.version == 6:

            class _IPv6HTTPServer(HTTPServer):
                address_family = socket.AF_INET6

            server_type = _IPv6HTTPServer

        server = server_type((self._host, self._port), _Handler)
        deadline = time.monotonic() + self._timeout_seconds
        requests_handled = 0
        exhausted_request_budget = False
        try:
            while captured is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if requests_handled >= self._max_requests:
                    exhausted_request_budget = True
                    break

                server.timeout = remaining
                await anyio.to_thread.run_sync(server.handle_request)
                if time.monotonic() < deadline:
                    requests_handled += 1
        finally:
            server.server_close()
            if self._expected_state == expected_state_value:
                self._expected_state = None

        if captured is None:
            if exhausted_request_budget:
                raise OAuthFlowError(
                    f"OAuth callback request limit exceeded on {self.redirect_uri}"
                )
            raise OAuthFlowError(f"timed out waiting for the OAuth callback on {self.redirect_uri}")

        if "error" in captured:
            raise OAuthFlowError("authorization server returned an OAuth error")

        return AuthorizationCodeResult(
            code=captured["code"], state=captured["state"], iss=captured.get("iss")
        )
