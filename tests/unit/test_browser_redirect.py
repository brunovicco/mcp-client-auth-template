"""Unit tests for :func:`open_system_browser`."""

import webbrowser

import pytest

from mcp_client_auth_template.adapters import browser_redirect

_URL = "https://as.example.invalid/authorize?client_id=abc"


async def test_prints_the_url_when_the_browser_opens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(webbrowser, "open", lambda url: True)

    await browser_redirect.open_system_browser(_URL)

    assert _URL in capsys.readouterr().out


async def test_prints_the_url_when_the_browser_fails_to_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(webbrowser, "open", lambda url: False)

    await browser_redirect.open_system_browser(_URL)

    assert _URL in capsys.readouterr().out
