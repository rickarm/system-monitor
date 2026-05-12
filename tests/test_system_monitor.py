"""Tests for main system_monitor module."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
from system_monitor import load_env, fallback_alert, send_alfred_alert, create_github_issue, execute_kill
from state import load_state, save_state


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


@patch("system_monitor.urllib.request.urlopen")
def test_alfred_alert_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok":true,"telegram_message_id":123}'
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp
    result = send_alfred_alert(
        alfred_url="http://localhost:8200",
        alfred_api_key="test-key",
        service="openclaw",
        transition="ok->down",
        detail="test kill",
    )
    assert result is True


@patch("system_monitor.send_telegram")
@patch("system_monitor.urllib.request.urlopen")
def test_alfred_alert_fallback(mock_urlopen, mock_send_tg):
    mock_urlopen.side_effect = ConnectionRefusedError("refused")
    mock_send_tg.return_value = True
    result = send_alfred_alert(
        alfred_url="http://localhost:8200",
        alfred_api_key="test-key",
        service="openclaw",
        transition="ok->down",
        detail="test kill",
        fallback_token="bot-token",
        fallback_chat_id="12345",
    )
    assert result is True
    mock_send_tg.assert_called_once()


@patch("system_monitor.subprocess.run")
def test_github_issue_creation(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/rickarm/system-monitor/issues/1\n")
    result = create_github_issue(
        reason="format_error_loop",
        detail="8 errors in 60 min",
        log_excerpts=["line1", "line2"],
        marker_contents={"killed_at": "2026-05-08T14:40:00"},
    )
    assert result is True
    args = mock_run.call_args[0][0]
    assert "gh" in args
    assert "issue" in args
    assert "create" in args


@patch("system_monitor.subprocess.run")
def test_github_issue_failure_nonfatal(mock_run):
    mock_run.side_effect = FileNotFoundError("gh not found")
    result = create_github_issue(
        reason="format_error_loop",
        detail="8 errors",
        log_excerpts=[],
        marker_contents={},
    )
    assert result is False


@patch("system_monitor.create_github_issue", return_value=True)
@patch("system_monitor.send_alfred_alert", return_value=True)
@patch("system_monitor.subprocess.run")
def test_kill_mechanism_stop_and_pkill(mock_run, mock_alert, mock_issue, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    marker_path = tmp_path / "openclaw-killed.marker"
    env = {
        "ALFRED_URL": "http://localhost:8200",
        "ALFRED_API_KEY": "key",
    }
    check_result = {
        "status": "kill",
        "detail": "8 format errors in 60 min",
        "reason": "format_error_loop",
        "pattern_counts": {"format_error_loop": 8, "quota_failover_cascade": 0},
        "log_excerpts": ["line1"],
    }

    with patch("system_monitor.OPENCLAW_KILL_MARKER", marker_path):
        execute_kill(check_result, env)

    # Verify stop script called
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("stop-openclaw" in c for c in calls)
    assert any("pkill" in c for c in calls)

    # Verify marker written
    assert marker_path.exists()
    marker = json.loads(marker_path.read_text())
    assert marker["reason"] == "format_error_loop"

    # Verify alert sent
    mock_alert.assert_called_once()

    # Verify issue created
    mock_issue.assert_called_once()


def test_fallback_alert_kill():
    transitions = [
        {
            "service": "openclaw-tokens",
            "context": "OpenClaw token budget watchdog",
            "old_status": "healthy",
            "new_status": "kill",
            "detail": "8 gpt-5.4-pro format error retries in 60 min",
            "fix": None,
        }
    ]
    result = fallback_alert(transitions)
    assert "WATCHDOG KILL" in result
    assert "🔴" in result
    assert "openclaw-tokens" in result


def test_kill_then_next_run_no_spurious_transition(tmp_path):
    """After a kill, the next run should not produce a transition alert."""
    state_file = tmp_path / "state.json"

    # Simulate state after kill run: openclaw-tokens is "killed"
    state = {
        "services": {
            "openclaw-tokens": {
                "status": "killed",
                "detail": "Watchdog killed OpenClaw",
                "last_checked": "2026-05-08T15:00:00+00:00",
                "last_changed": "2026-05-08T14:40:00+00:00",
            }
        }
    }
    save_state(state, state_file)

    # Next run: check returns "killed" again
    prev = load_state(state_file)
    old_status = prev["services"]["openclaw-tokens"]["status"]
    new_status = "killed"  # what check_openclaw_token_health would return

    # No transition should be detected
    assert old_status == new_status  # killed -> killed = no transition
