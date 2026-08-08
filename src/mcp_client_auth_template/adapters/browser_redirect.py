"""Redirect handler for ``OAuthClientProvider`` (``mcp.client.auth``).

Opens the authorization URL in the system's default browser. In a headless
environment (CI, a remote shell with no display) ``webbrowser.open`` returns
``False`` instead of raising, so the URL is always also printed - the person
running the demo can copy it into a browser on another machine.
"""

import webbrowser

import structlog

logger = structlog.get_logger(__name__)


async def open_system_browser(authorization_url: str) -> None:
    """Open ``authorization_url`` in the system browser; print it either way.

    Matches the ``redirect_handler`` signature ``OAuthClientProvider`` expects:
    ``Callable[[str], Awaitable[None]]``.
    """
    opened = webbrowser.open(authorization_url)
    if not opened:
        logger.info("browser_open_failed", url=authorization_url)
    print(f"Open this URL to authorize: {authorization_url}")
