"""Tests for Telegram send helper."""

from unittest.mock import patch, MagicMock
from telegram import send_telegram, format_degraded_context


def test_format_degraded_context_single():
    transitions = [
        {
            "service": "sherlock-hq",
            "context": "FastAPI dashboard (port 8300)",
            "old_status": "healthy",
            "new_status": "degraded",
            "detail": "HTTP 503 (expected 200)",
            "fix": "launchctl kickstart ...",
        }
    ]
    result = format_degraded_context(transitions)
    assert "sherlock-hq" in result
    assert "HTTP 503" in result
    assert "healthy" in result
    assert "degraded" in result


def test_format_degraded_context_recovery():
    transitions = [
        {
            "service": "openclaw",
            "context": "Mandy Telegram bot agent",
            "old_status": "degraded",
            "new_status": "healthy",
            "detail": "Loaded and process running",
            "fix": None,
        }
    ]
    result = format_degraded_context(transitions)
    assert "openclaw" in result
    assert "RECOVERED" in result or "healthy" in result


@patch("telegram.urllib.request.urlopen")
def test_send_telegram_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok": true}'
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp
    assert send_telegram("token", "123", "hello") is True


@patch("telegram.urllib.request.urlopen")
def test_send_telegram_failure(mock_urlopen):
    mock_urlopen.side_effect = Exception("network error")
    assert send_telegram("token", "123", "hello") is False
