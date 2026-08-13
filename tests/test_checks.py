"""Tests for deterministic health checks."""

import json
import textwrap
from unittest.mock import patch, MagicMock
from checks import (
    check_sherlock_hq,
    check_sleep_watcher,
    check_openclaw,
    check_openclaw_token_health,
    check_peloton_sync,
    check_git_pull_repos,
    CHECKS,
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


# ── Watchdog fixtures ───────────────────────────────────────────────────────────
# These are REAL signatures captured from ~/scripts/logs/openclaw.log on 2026-08-11, not
# invented ones. The previous fixtures used `gpt-5.4-pro` lines, which Mandy stopped
# emitting when she moved to Anthropic on 2026-06-26 — so the tests kept passing against
# patterns that could no longer match anything in production. The suite validated the bug
# for six weeks. Keep these anchored to strings the gateway actually emits.

def _ts_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")


def _poison_line(ts=None):
    """Schema-rejection retry loop: the runaway that IS worth killing over."""
    ts = ts or _ts_now()
    return (f"{ts} [agent/embedded] embedded run agent end: runId=abc "
            f"isError=true model=claude-sonnet-4-6 provider=anthropic "
            f"error=LLM request failed: provider rejected the request schema "
            f"or tool payload.\n")


def _usage_cap_line(ts=None):
    """Console spend cap reached: alert-worthy, but NOT kill-worthy."""
    ts = ts or _ts_now()
    return (f"{ts} [model-fallback/decision] model fallback decision: "
            f"decision=candidate_failed requested=anthropic/claude-sonnet-4-6 "
            f"candidate=anthropic/claude-sonnet-4-6 reason=rate_limit "
            f"providerErrorType=invalid_request_error next=none "
            f"detail=You have reached your specified API usage limits.\n")


def _json_line(msg, ts=None):
    """The structured JSON sink format the log gained on 2026-08-11 via logging.file."""
    ts = ts or _ts_now()
    # Compact separators on purpose: the gateway emits `"time":"..."` with no space, and
    # the fixture must mirror production rather than json.dumps' prettier default.
    return json.dumps({
        "0": '{"subsystem":"agent/embedded"}',
        "1": msg,
        "time": ts,
        "message": msg,
    }, separators=(",", ":")) + "\n"


def test_openclaw_token_health_format_errors(tmp_path):
    """Poison-pill loop above threshold must kill. Model-agnostic: the line says
    claude-sonnet-4-6, and no model name appears in the pattern."""
    log = tmp_path / "openclaw.log"
    log.write_text(_poison_line() * 7)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "kill"
    assert result["reason"] == "poison_loop"
    assert result["pattern_counts"]["poison_loop"] == 7


def test_openclaw_token_health_below_threshold(tmp_path):
    log = tmp_path / "openclaw.log"
    log.write_text(_poison_line() * 3)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_token_health_usage_cap_degrades_never_kills(tmp_path):
    """Hitting the Console spend cap must NOT kill. The provider has already stopped
    serving requests, so spend has stopped by itself; killing Mandy would cost Rick his
    assistant and save nothing. This is the behaviour change from the old design, which
    treated provider refusals as a kill trigger."""
    log = tmp_path / "openclaw.log"
    log.write_text(_usage_cap_line() * 9)   # well above the old kill threshold of 4
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "degraded"
    assert "spend cap" in result["detail"]


def test_openclaw_token_health_overloaded_is_ignored(tmp_path):
    """`reason=overloaded` is the TRANSIENT provider error and self-resolves. It must
    match neither pattern, at any volume."""
    log = tmp_path / "openclaw.log"
    ts = _ts_now()
    line = (f"{ts} [model-fallback/decision] model fallback decision: "
            f"decision=candidate_failed requested=anthropic/claude-sonnet-4-6 "
            f"reason=overloaded next=none\n")
    log.write_text(line * 20)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_token_health_reads_json_log_format(tmp_path):
    """Regression: the log gained JSON lines on 2026-08-11 (logging.file). If the
    timestamp cannot be parsed from them, the scan window silently becomes 'the entire
    file' and stale incidents count toward a live kill threshold."""
    log = tmp_path / "openclaw.log"
    msg = ("embedded run agent end: runId=abc isError=true model=claude-sonnet-4-6 "
           "provider=anthropic error=LLM request failed: provider rejected the request "
           "schema or tool payload.")
    log.write_text(_json_line(msg) * 7)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "kill"
    assert result["pattern_counts"]["poison_loop"] == 7


def test_openclaw_token_health_json_window_excludes_old(tmp_path):
    """The window must actually bound JSON lines too, not just parse them."""
    log = tmp_path / "openclaw.log"
    msg = ("embedded run agent end: runId=abc isError=true model=claude-sonnet-4-6 "
           "provider=anthropic error=LLM request failed: provider rejected the request "
           "schema or tool payload.")
    log.write_text(_json_line(msg, ts="2020-01-01T00:00:00.000-07:00") * 20)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"


def test_openclaw_token_health_unparseable_log_reports_degraded(tmp_path):
    """Silence must not look like health. If no line in the window carries a parseable
    timestamp, the format has drifted and the thresholds cannot be trusted — say so
    rather than reporting healthy. This is the exact state the watchdog was in on the
    morning of 2026-08-11."""
    log = tmp_path / "openclaw.log"
    log.write_text("some log format we do not understand\n" * 5)
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "degraded"
    assert "timestamps" in result["detail"]


def test_openclaw_token_health_old_errors(tmp_path):
    log = tmp_path / "openclaw.log"
    log.write_text(_poison_line(ts="2020-01-01T00:00:00.000-07:00") * 10)
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
