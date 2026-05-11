"""Tests for Claude reasoning module."""

from reasoning import build_prompt


def test_build_prompt_single_degraded():
    transitions = [
        {
            "service": "sherlock-hq",
            "context": "FastAPI dashboard (port 8300)",
            "old_status": "healthy",
            "new_status": "degraded",
            "detail": "Connection failed: ConnectionRefusedError",
            "fix": "launchctl kickstart -k gui/$(id -u)/com.rickarmbrust.sherlock-hq",
        }
    ]
    prompt = build_prompt(transitions)
    assert "sherlock-hq" in prompt
    assert "ConnectionRefusedError" in prompt
    assert "Telegram" in prompt


def test_build_prompt_recovery():
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
    prompt = build_prompt(transitions)
    assert "recovered" in prompt.lower() or "healthy" in prompt.lower()


def test_build_prompt_multiple():
    transitions = [
        {
            "service": "sherlock-hq",
            "context": "FastAPI dashboard (port 8300)",
            "old_status": "healthy",
            "new_status": "degraded",
            "detail": "HTTP 503",
            "fix": "restart",
        },
        {
            "service": "openclaw",
            "context": "Mandy Telegram bot agent",
            "old_status": "healthy",
            "new_status": "degraded",
            "detail": "No PID",
            "fix": "kickstart",
        },
    ]
    prompt = build_prompt(transitions)
    assert "sherlock-hq" in prompt
    assert "openclaw" in prompt
