"""Loopback callback server for native/CLI OAuth clients (RFC 8252 §7.3).

A CLI has no web origin to receive the authorization redirect at, so the
standard pattern is a local HTTP listener on ``127.0.0.1`` that the
authorization server redirects the browser back to. This server accepts
exactly one request, extracts ``code``/``state``/``iss`` (or ``error``) from
its query string, and returns an ``AuthorizationCodeResult`` matching the
``callback_handler`` contract ``OAuthClientProvider`` expects:
``Callable[[], Awaitable[AuthorizationCodeResult]]``.

The registered ``redirect_uri`` (in the client's ``OAuthClientMetadata`` or,
for Entra, in the app registration) must point at this exact
``http://<host>:<port><path>``.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import anyio
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult

_SUCCESS_BODY = b"<html><body><h1>Authorization complete</h1>You can close this tab.</body></html>"
_ERROR_BODY = b"<html><body><h1>Authorization failed</h1>You can close this tab.</body></html>"


class LoopbackCallbackServer:
    """Waits for one OAuth authorization-code redirect on ``http://host:port/path``."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/callback",
        timeout_seconds: float = 300,
    ) -> None:
        """Bind nothing yet; the socket opens lazily inside ``wait_for_callback``."""
        self._host = host
        self._port = port
        self._path = path
        self._timeout_seconds = timeout_seconds

    @property
    def redirect_uri(self) -> str:
        """The URI to register as this client's ``redirect_uri``."""
        return f"http://{self._host}:{self._port}{self._path}"

    async def wait_for_callback(self) -> AuthorizationCodeResult:
        """Block until the authorization redirect arrives, or raise on timeout/error.

        Raises:
            OAuthFlowError: No callback arrived within ``timeout_seconds``, the
                callback hit the wrong path, or the authorization server
                reported an ``error`` instead of a ``code``.
        """
        captured: dict[str, str] = {}
        expected_path = self._path

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    return

                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                captured.update(query)

                self.send_response(200 if "code" in query else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_SUCCESS_BODY if "code" in query else _ERROR_BODY)

            def log_message(self, format_str: str, *args: object) -> None:
                """Silence the default stderr access log; nothing here is worth keeping."""

        server = HTTPServer((self._host, self._port), _Handler)
        server.timeout = self._timeout_seconds
        try:
            await anyio.to_thread.run_sync(server.handle_request)
        finally:
            server.server_close()

        if not captured:
            raise OAuthFlowError(f"timed out waiting for the OAuth callback on {self.redirect_uri}")
        if "error" in captured:
            description = captured.get("error_description", captured["error"])
            raise OAuthFlowError(f"authorization server returned an error: {description}")
        if "code" not in captured:
            raise OAuthFlowError(
                f"callback on {self.redirect_uri} had no 'code' or 'error' parameter"
            )

        return AuthorizationCodeResult(
            code=captured["code"], state=captured.get("state"), iss=captured.get("iss")
        )
