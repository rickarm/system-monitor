"""Tests for main system_monitor module."""

from datetime import datetime
from pathlib import Path
from system_monitor import load_env, fallback_alert


def test_load_env_missing_file(tmp_path):
    result = load_env(tmp_path / "nonexistent")
    assert result == {}


def test_load_env_basic(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nBAZ=qux\n")
    result = load_env(env_file)
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_load_env_strips_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('TOKEN="my-secret"\nKEY=\'another\'\n')
    result = load_env(env_file)
    assert result == {"TOKEN": "my-secret", "KEY": "another"}


def test_load_env_skips_comments_and_blanks(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nVALID=yes\n")
    result = load_env(env_file)
    assert result == {"VALID": "yes"}


def test_fallback_alert_degraded():
    transitions = [
        {
            "service": "sherlock-hq",
            "context": "FastAPI dashboard (port 8300)",
            "old_status": "healthy",
            "new_status": "degraded",
            "detail": "HTTP 503",
            "fix": "launchctl kickstart ...",
        }
    ]
    result = fallback_alert(transitions)
    assert "DEGRADED" in result
    assert "sherlock-hq" in result
    assert "HTTP 503" in result
    assert "<b>" in result
    assert "<code>" in result


def test_fallback_alert_recovered():
    transitions = [
        {
            "service": "openclaw",
            "context": "Mandy Telegram bot agent",
            "old_status": "degraded",
            "new_status": "healthy",
            "detail": "Loaded and running",
            "fix": None,
        }
    ]
    result = fallback_alert(transitions)
    assert "RECOVERED" in result
    assert "openclaw" in result
    assert "<code>" not in result  # no fix command


def test_fallback_alert_multiple():
    transitions = [
        {
            "service": "a",
            "context": "ctx-a",
            "old_status": "healthy",
            "new_status": "degraded",
            "detail": "down",
            "fix": "fix-a",
        },
        {
            "service": "b",
            "context": "ctx-b",
            "old_status": "degraded",
            "new_status": "healthy",
            "detail": "up",
            "fix": None,
        },
    ]
    result = fallback_alert(transitions)
    assert "a" in result
    assert "b" in result
