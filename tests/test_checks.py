"""Tests for deterministic health checks."""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock
from checks import (
    check_sherlock_hq,
    check_sleep_watcher,
    check_openclaw,
    check_peloton_sync,
    check_git_pull_repos,
    CHECKS,
)


def test_checks_registry():
    assert len(CHECKS) == 5
    assert set(CHECKS.keys()) == {
        "sherlock-hq", "sleep-watcher", "openclaw",
        "peloton-sync", "git-pull-repos",
    }


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
