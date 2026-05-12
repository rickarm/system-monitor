"""Tests for deterministic health checks."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock
from checks import (
    check_sherlock_hq,
    check_sleep_watcher,
    check_openclaw,
    check_openclaw_token_health,
    check_peloton_sync,
    check_git_pull_repos,
    CHECKS,
    OPENCLAW_KILL_MARKER,
)


def test_checks_registry():
    assert len(CHECKS) == 6
    assert set(CHECKS.keys()) == {
        "openclaw-tokens", "sherlock-hq", "sleep-watcher", "openclaw",
        "peloton-sync", "git-pull-repos",
    }


def test_checks_registry_with_token_watchdog():
    assert len(CHECKS) == 6
    keys = list(CHECKS.keys())
    assert "openclaw-tokens" in keys
    assert keys.index("openclaw-tokens") < keys.index("openclaw")


@patch("checks.http.client.HTTPConnection")
def test_sherlock_hq_healthy(mock_conn_cls):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp
    mock_conn_cls.return_value = mock_conn
    result = check_sherlock_hq()
    assert result["status"] == "healthy"


@patch("checks.http.client.HTTPConnection")
def test_sherlock_hq_bad_status(mock_conn_cls):
    mock_resp = MagicMock()
    mock_resp.status = 503
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp
    mock_conn_cls.return_value = mock_conn
    result = check_sherlock_hq()
    assert result["status"] == "degraded"
    assert "fix" in result


@patch("checks.http.client.HTTPConnection")
def test_sherlock_hq_connection_error(mock_conn_cls):
    mock_conn_cls.side_effect = ConnectionRefusedError("refused")
    result = check_sherlock_hq()
    assert result["status"] == "degraded"


@patch("checks.subprocess.run")
def test_sleep_watcher_not_loaded(mock_run):
    mock_run.return_value = MagicMock(returncode=1)
    result = check_sleep_watcher()
    assert result["status"] == "degraded"
    assert "not loaded" in result["detail"]


@patch("checks.subprocess.run")
def test_sleep_watcher_log_missing(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    with patch("checks.SLEEP_WATCHER_LOG", tmp_path / "nonexistent.log"):
        result = check_sleep_watcher()
    assert result["status"] == "degraded"
    assert "not found" in result["detail"]


@patch("checks.subprocess.run")
def test_openclaw_not_loaded(mock_run):
    mock_run.return_value = MagicMock(returncode=1)
    result = check_openclaw()
    assert result["status"] == "degraded"


@patch("checks.subprocess.run")
def test_openclaw_running(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{\n\t"PID" = 12345;\n\t"Label" = "com.rickarmbrust.openclaw";\n}',
    )
    result = check_openclaw()
    assert result["status"] == "healthy"


def test_peloton_sync_log_missing(tmp_path):
    with patch("checks.PELOTON_SYNC_LOG", tmp_path / "nope.log"):
        result = check_peloton_sync()
    assert result["status"] == "degraded"


def test_peloton_sync_success(tmp_path):
    log = tmp_path / "peloton-sync.log"
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write_text(f"[{ts}] SUCCESS: synced 3 workouts\n")
    with patch("checks.PELOTON_SYNC_LOG", log):
        result = check_peloton_sync()
    assert result["status"] == "healthy"


def test_git_pull_no_log(tmp_path):
    with patch("checks.GIT_PULL_LOG", tmp_path / "nope.log"):
        result = check_git_pull_repos()
    assert result["status"] == "degraded"


def test_git_pull_success(tmp_path):
    log = tmp_path / "git-pull.log"
    log.write_text(textwrap.dedent("""\
        === git-pull-repos: 2026-05-10 03:00 ===
        Pulling repo1... ok
        DONE updated=2 failed=0 skipped=1
    """))
    with patch("checks.GIT_PULL_LOG", log):
        result = check_git_pull_repos()
    assert result["status"] == "healthy"


def test_git_pull_failures(tmp_path):
    log = tmp_path / "git-pull.log"
    log.write_text(textwrap.dedent("""\
        === git-pull-repos: 2026-05-10 03:00 ===
        Pulling repo1... FAIL
        DONE updated=1 failed=1 skipped=0
    """))
    with patch("checks.GIT_PULL_LOG", log):
        result = check_git_pull_repos()
    assert result["status"] == "degraded"
    assert "1" in result["detail"]


def test_openclaw_token_health_clean(tmp_path):
    log = tmp_path / "openclaw.log"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")
    log.write_text(f"{ts} [gateway] ready\n{ts} [heartbeat] started\n")
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_token_health_no_log(tmp_path):
    with patch("checks.OPENCLAW_LOG", tmp_path / "nope.log"), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_token_health_format_errors(tmp_path):
    log = tmp_path / "openclaw.log"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")
    line = (f"{ts} [agent/embedded] embedded run agent end: runId=abc "
            f"isError=true model=gpt-5.4-pro provider=openai "
            f"error=LLM request failed rawError=400 Item 'msg_abc' "
            f"of type 'message' was provided without its required "
            f"'reasoning' item: 'rs_abc'.\n")
    log.write_text(line * 7)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "kill"
    assert result["reason"] == "format_error_loop"
    assert result["pattern_counts"]["format_error_loop"] == 7


def test_openclaw_token_health_below_threshold(tmp_path):
    log = tmp_path / "openclaw.log"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")
    line = (f"{ts} [agent/embedded] embedded run agent end: runId=abc "
            f"isError=true model=gpt-5.4-pro provider=openai "
            f"error=LLM request failed rawError=400 Item 'msg_abc' "
            f"of type 'message' was provided without its required "
            f"'reasoning' item: 'rs_abc'.\n")
    log.write_text(line * 3)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_token_health_quota_failover(tmp_path):
    log = tmp_path / "openclaw.log"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")
    line = (f"{ts} [model-fallback/decision] model fallback decision: "
            f"decision=candidate_failed requested=openai/gpt-5.4-nano "
            f"candidate=openai/gpt-5.4-nano reason=rate_limit "
            f"next=openai/gpt-5.4-pro detail=quota exceeded\n")
    log.write_text(line * 5)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "kill"
    assert result["reason"] == "quota_failover_cascade"
    assert result["pattern_counts"]["quota_failover_cascade"] == 5


def test_openclaw_token_health_old_errors(tmp_path):
    log = tmp_path / "openclaw.log"
    old_ts = "2020-01-01T00:00:00.000-07:00"
    line = (f"{old_ts} [agent/embedded] embedded run agent end: runId=abc "
            f"isError=true model=gpt-5.4-pro provider=openai "
            f"error=LLM request failed rawError=400 Item 'msg_abc' "
            f"of type 'message' was provided without its required "
            f"'reasoning' item: 'rs_abc'.\n")
    log.write_text(line * 10)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_killed_marker_process_check(tmp_path):
    marker = tmp_path / "openclaw-killed.marker"
    marker.write_text(json.dumps({
        "killed_at": "2026-05-08T14:40:00+00:00",
        "reason": "format_error_loop",
        "detail": "test",
        "pattern_counts": {},
    }))
    with patch("checks.OPENCLAW_KILL_MARKER", marker):
        result = check_openclaw()
    assert result["status"] == "killed"
    assert "format_error_loop" in result["detail"]
    assert "fix" in result


def test_openclaw_token_health_killed_marker(tmp_path):
    log = tmp_path / "openclaw.log"
    log.write_text("")
    marker = tmp_path / "openclaw-killed.marker"
    marker.write_text(json.dumps({
        "killed_at": "2026-05-08T14:40:00+00:00",
        "reason": "format_error_loop",
        "detail": "8 gpt-5.4-pro format error retries in 60 min",
        "pattern_counts": {"format_error_loop": 8, "quota_failover_cascade": 3},
    }))
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", marker):
        result = check_openclaw_token_health()
    assert result["status"] == "killed"
    assert "format_error_loop" in result["detail"]
    assert "fix" in result
