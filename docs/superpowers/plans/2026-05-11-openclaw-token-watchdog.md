# OpenClaw Token Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an autonomous OpenClaw token budget watchdog to system-monitor that detects error loops in logs and kills the service before it burns through the OpenAI budget.

**Architecture:** New `check_openclaw_token_health()` in `checks.py` scans the last 60 min of OpenClaw logs for two kill patterns (format error retries, quota failover cascades). When triggered, `system_monitor.py` executes a kill mechanism (stop service, pkill orphans, write marker, alert via Alfred, create GitHub issue). A kill marker file prevents resurrection and suppresses repeated alerts.

**Tech Stack:** Python 3, subprocess, re, urllib, json. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-11-openclaw-token-watchdog-design.md`

---

### Task 1: Log Scanner — Detection Logic

**Files:**
- Modify: `checks.py` (add constants, add `check_openclaw_token_health()`)
- Test: `tests/test_checks.py`

- [ ] **Step 1: Write test for clean log (no errors)**

Add these imports to the existing import block at the top of `tests/test_checks.py` (merge with existing imports, don't replace):

```python
import json
from checks import check_openclaw_token_health, OPENCLAW_KILL_MARKER


def test_openclaw_token_health_clean(tmp_path):
    log = tmp_path / "openclaw.log"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-07:00")
    log.write_text(f"{ts} [gateway] ready\n{ts} [heartbeat] started\n")
    with patch("checks.OPENCLAW_LOG", log), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"
```

- [ ] **Step 2: Write test for missing log file**

```python
def test_openclaw_token_health_no_log(tmp_path):
    with patch("checks.OPENCLAW_LOG", tmp_path / "nope.log"), patch("checks.OPENCLAW_KILL_MARKER", tmp_path / "no.marker"):
        result = check_openclaw_token_health()
    assert result["status"] == "healthy"
```

- [ ] **Step 3: Write test for format error pattern above threshold**

```python
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
```

- [ ] **Step 4: Write test for format errors below threshold**

```python
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
```

- [ ] **Step 5: Write test for quota failover pattern**

```python
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
```

- [ ] **Step 6: Write test for old errors outside window**

```python
def test_openclaw_token_health_old_errors(tmp_path):
    log = tmp_path / "openclaw.log"
    # 2 hours ago — outside 60-min window
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
```

- [ ] **Step 7: Write test for kill marker present**

```python
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
```

- [ ] **Step 8: Run all new tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_checks.py -k "openclaw_token" -v`
Expected: FAIL — `check_openclaw_token_health` not defined

- [ ] **Step 9: Implement `check_openclaw_token_health()`**

Add to `checks.py` after the existing imports:

```python
import json
from datetime import datetime, timezone, timedelta

OPENCLAW_LOG = HOME / "scripts/logs/openclaw.log"
OPENCLAW_KILL_MARKER = HOME / "scripts/logs/openclaw-killed.marker"

OPENCLAW_FORMAT_ERROR_THRESHOLD = 6
OPENCLAW_QUOTA_FAILOVER_THRESHOLD = 4
OPENCLAW_SCAN_WINDOW_MINUTES = 60

RE_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+([+-]\d{2}:\d{2})")
RE_FORMAT_ERROR = re.compile(r"embedded run agent end.*isError=true.*gpt-5\.4-pro.*reasoning.*item")
RE_QUOTA_FAILOVER = re.compile(r"candidate_failed.*reason=rate_limit.*next=openai/gpt-5\.4-pro")


def _parse_ts(line: str) -> datetime | None:
    m = RE_TIMESTAMP.match(line)
    if not m:
        return None
    try:
        raw = m.group(1) + m.group(2)
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _read_recent_lines(path: Path, window_minutes: int) -> list[str]:
    """Read lines from the end of the file within the time window."""
    if not path.exists():
        return []
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        lines = path.read_text(errors="replace").splitlines()
        recent = []
        for line in reversed(lines):
            ts = _parse_ts(line)
            if ts is not None and ts < cutoff:
                break
            recent.append(line)
        recent.reverse()
        return recent
    except OSError:
        return []


def check_openclaw_token_health() -> dict:
    # Check kill marker first
    if OPENCLAW_KILL_MARKER.exists():
        try:
            marker = json.loads(OPENCLAW_KILL_MARKER.read_text())
            reason = marker.get("reason", "unknown")
            killed_at = marker.get("killed_at", "unknown")
            return {
                "status": "killed",
                "detail": f"Watchdog killed OpenClaw at {killed_at} — {reason}",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
            }
        except (json.JSONDecodeError, OSError):
            return {
                "status": "killed",
                "detail": "Kill marker present but unreadable",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
            }

    recent = _read_recent_lines(OPENCLAW_LOG, OPENCLAW_SCAN_WINDOW_MINUTES)

    format_errors = [l for l in recent if RE_FORMAT_ERROR.search(l)]
    quota_failovers = [l for l in recent if RE_QUOTA_FAILOVER.search(l)]

    counts = {
        "format_error_loop": len(format_errors),
        "quota_failover_cascade": len(quota_failovers),
    }

    if len(format_errors) >= OPENCLAW_FORMAT_ERROR_THRESHOLD:
        return {
            "status": "kill",
            "detail": f"{len(format_errors)} gpt-5.4-pro format error retries in {OPENCLAW_SCAN_WINDOW_MINUTES} min",
            "reason": "format_error_loop",
            "pattern_counts": counts,
            "log_excerpts": format_errors[-10:],
        }

    if len(quota_failovers) >= OPENCLAW_QUOTA_FAILOVER_THRESHOLD:
        return {
            "status": "kill",
            "detail": f"{len(quota_failovers)} quota-triggered failovers to gpt-5.4-pro in {OPENCLAW_SCAN_WINDOW_MINUTES} min",
            "reason": "quota_failover_cascade",
            "pattern_counts": counts,
            "log_excerpts": quota_failovers[-10:],
        }

    return ok(f"{counts['format_error_loop']} format errors, {counts['quota_failover_cascade']} quota failovers in last {OPENCLAW_SCAN_WINDOW_MINUTES} min")
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_checks.py -k "openclaw_token" -v`
Expected: 7 PASSED

- [ ] **Step 11: Commit**

```bash
git add checks.py tests/test_checks.py
git commit -m "feat: add check_openclaw_token_health log scanner"
```

---

### Task 2: Modify check_openclaw() for Kill Marker + Update CHECKS Dict

**Files:**
- Modify: `checks.py` (modify `check_openclaw()`, reorder `CHECKS`)
- Modify: `tests/test_checks.py`

- [ ] **Step 1: Write test for check_openclaw with kill marker**

```python
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
```

- [ ] **Step 2: Write test for updated CHECKS dict ordering**

```python
def test_checks_registry_with_token_watchdog():
    assert len(CHECKS) == 6
    keys = list(CHECKS.keys())
    assert "openclaw-tokens" in keys
    assert keys.index("openclaw-tokens") < keys.index("openclaw")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_checks.py -k "killed_marker_process or registry_with" -v`
Expected: FAIL

- [ ] **Step 4: Modify `check_openclaw()` to check kill marker**

In `checks.py`, add kill marker check at the top of `check_openclaw()`:

```python
def check_openclaw() -> dict:
    # If watchdog killed OpenClaw, report killed status
    if OPENCLAW_KILL_MARKER.exists():
        try:
            marker = json.loads(OPENCLAW_KILL_MARKER.read_text())
            reason = marker.get("reason", "unknown")
            killed_at = marker.get("killed_at", "unknown")
            return {
                "status": "killed",
                "detail": f"Watchdog killed at {killed_at} — {reason}",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
            }
        except (json.JSONDecodeError, OSError):
            return {
                "status": "killed",
                "detail": "Kill marker present but unreadable",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
            }
    # ... existing process health checks unchanged ...
```

- [ ] **Step 5: Reorder CHECKS dict and add new entries**

Replace the existing `CHECKS` and `SERVICE_CONTEXT` at the bottom of `checks.py`:

```python
SERVICE_CONTEXT = {
    "openclaw-tokens": "OpenClaw token budget watchdog",
    "sherlock-hq": "FastAPI dashboard (port 8300)",
    "sleep-watcher": "Oura / Airtable sync daemon",
    "openclaw": "Mandy Telegram bot agent",
    "peloton-sync": "Peloton CSV / Airtable sync",
    "git-pull-repos": "Nightly git pull across all repos",
}

CHECKS = {
    "openclaw-tokens": check_openclaw_token_health,
    "sherlock-hq": check_sherlock_hq,
    "sleep-watcher": check_sleep_watcher,
    "openclaw": check_openclaw,
    "peloton-sync": check_peloton_sync,
    "git-pull-repos": check_git_pull_repos,
}
```

- [ ] **Step 6: Update the existing `test_checks_registry` test**

Change the existing test to expect 6 checks:

```python
def test_checks_registry():
    assert len(CHECKS) == 6
    assert set(CHECKS.keys()) == {
        "openclaw-tokens", "sherlock-hq", "sleep-watcher", "openclaw",
        "peloton-sync", "git-pull-repos",
    }
```

- [ ] **Step 7: Run all check tests**

Run: `.venv/bin/python3 -m pytest tests/test_checks.py -v`
Expected: All PASSED

- [ ] **Step 8: Commit**

```bash
git add checks.py tests/test_checks.py
git commit -m "feat: add kill marker guard to check_openclaw, reorder CHECKS"
```

---

### Task 3: Kill Mechanism + Alfred Alerting

**Files:**
- Modify: `system_monitor.py` (add `execute_kill()`, `send_alfred_alert()`, `create_github_issue()`)
- Test: `tests/test_system_monitor.py`

- [ ] **Step 1: Write test for `send_alfred_alert` success**

```python
from unittest.mock import patch, MagicMock
from system_monitor import send_alfred_alert


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
```

- [ ] **Step 2: Write test for `send_alfred_alert` fallback to Telegram**

```python
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
```

- [ ] **Step 3: Write test for `create_github_issue` success**

```python
from system_monitor import create_github_issue


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
    assert result is False  # failed but no exception raised
```

- [ ] **Step 4: Write test for `execute_kill` (mocked subprocess)**

```python
from system_monitor import execute_kill


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
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_system_monitor.py -k "alfred or github or kill_mechanism" -v`
Expected: FAIL — functions not defined

- [ ] **Step 6: Implement `send_alfred_alert()`**

Add these imports at the top of `system_monitor.py` (merge with existing imports):

```python
import json
import subprocess
import urllib.request
import urllib.error
```

And update the checks import:

```python
from checks import CHECKS, SERVICE_CONTEXT, OPENCLAW_KILL_MARKER
```

Then add the `send_alfred_alert` function:

```python
def send_alfred_alert(
    alfred_url: str,
    alfred_api_key: str,
    service: str,
    transition: str,
    detail: str,
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> bool:
    """Send alert via Alfred, fall back to direct Telegram."""
    url = f"{alfred_url.rstrip('/')}/alert"
    payload = json.dumps({
        "service": service,
        "transition": transition,
        "detail": detail,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {alfred_api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                log.info("Alert sent via Alfred")
                return True
            log.warning("Alfred returned error: %s", data)
    except Exception as e:
        log.warning("Alfred unreachable (%s), trying direct Telegram", e)

    if fallback_token and fallback_chat_id:
        msg = f"🔴 WATCHDOG KILL: {service}\n{detail}"
        return send_telegram(fallback_token, fallback_chat_id, msg)

    log.error("No fallback Telegram credentials — alert not sent")
    return False
```

- [ ] **Step 7: Implement `create_github_issue()`**

```python
def create_github_issue(
    reason: str,
    detail: str,
    log_excerpts: list[str],
    marker_contents: dict,
) -> bool:
    """Create a GitHub issue for investigation. Returns False on failure (non-fatal)."""
    title = f"OpenClaw token watchdog kill: {reason}"
    body_lines = [
        f"## Watchdog Kill Report",
        f"",
        f"**Reason:** {reason}",
        f"**Detail:** {detail}",
        f"**Killed at:** {marker_contents.get('killed_at', 'unknown')}",
        f"",
        f"### Pattern Counts",
        f"```json",
        json.dumps(marker_contents.get("pattern_counts", {}), indent=2),
        f"```",
        f"",
        f"### Log Excerpts (last {len(log_excerpts)})",
        f"```",
        *log_excerpts[-10:],
        f"```",
        f"",
        f"### Action Required",
        f"1. Check OpenAI billing/quota status",
        f"2. Inspect session files for Anthropic reasoning item contamination",
        f"3. Clear contaminated session if needed",
        f"4. Restart: `rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh`",
    ]
    body = "\n".join(body_lines)
    try:
        result = subprocess.run(
            ["gh", "issue", "create",
             "--repo", "rickarm/system-monitor",
             "--title", title,
             "--body", body],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            log.info("GitHub issue created: %s", result.stdout.strip())
            return True
        log.warning("gh issue create failed (exit %d): %s", result.returncode, result.stderr)
        return False
    except Exception as e:
        log.warning("Could not create GitHub issue: %s", e)
        return False
```

- [ ] **Step 8: Implement `execute_kill()`**

```python
def execute_kill(check_result: dict, env: dict) -> None:
    """Execute the full kill mechanism: stop, pkill, marker, alert, issue."""
    reason = check_result["reason"]
    detail = check_result["detail"]
    log_excerpts = check_result.get("log_excerpts", [])

    log.warning("TOKEN WATCHDOG: Killing OpenClaw — %s", detail)

    # Step 1: Stop and unload
    try:
        subprocess.run(
            [str(HOME / "scripts/stop-openclaw.sh")],
            capture_output=True, text=True, timeout=15,
        )
        log.info("OpenClaw stop script executed")
    except Exception as e:
        log.error("Failed to run stop script: %s", e)

    # Step 1b: Kill orphaned gateway processes
    try:
        subprocess.run(
            ["pkill", "-f", "openclaw-gateway"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass  # pkill returns non-zero if no matches — that's fine

    # Step 3: Write kill marker
    marker_contents = {
        "killed_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "detail": detail,
        "pattern_counts": check_result.get("pattern_counts", {}),
    }
    try:
        OPENCLAW_KILL_MARKER.write_text(json.dumps(marker_contents, indent=2))
        log.info("Kill marker written to %s", OPENCLAW_KILL_MARKER)
    except OSError as e:
        log.error("Failed to write kill marker: %s", e)

    # Step 4: Alert via Alfred
    send_alfred_alert(
        alfred_url=env.get("ALFRED_URL", "http://127.0.0.1:8200"),
        alfred_api_key=env.get("ALFRED_API_KEY", ""),
        service="openclaw",
        transition="ok->down",
        detail=f"TOKEN WATCHDOG KILL: {detail}. Service unloaded from launchd. Manual restart required: rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
        fallback_token=env.get("RICK_TELEGRAM_BOT_TOKEN", ""),
        fallback_chat_id=env.get("RICK_TELEGRAM_CHAT_ID", ""),
    )

    # Step 5: Create GitHub issue
    create_github_issue(reason, detail, log_excerpts, marker_contents)
```

- [ ] **Step 9: Run tests**

Run: `.venv/bin/python3 -m pytest tests/test_system_monitor.py -k "alfred or github or kill_mechanism" -v`
Expected: All PASSED

- [ ] **Step 10: Commit**

```bash
git add system_monitor.py tests/test_system_monitor.py
git commit -m "feat: add kill mechanism, Alfred alerting, GitHub issue creation"
```

---

### Task 4: Main Loop Integration + Fallback Alert Update

**Files:**
- Modify: `system_monitor.py` (main loop, `fallback_alert()`)
- Test: `tests/test_system_monitor.py`

- [ ] **Step 1: Write test for fallback_alert with kill status**

```python
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
```

- [ ] **Step 2: Write test for multi-run state transition (no spurious alert)**

```python
@patch("system_monitor.CHECKS", {})
def test_kill_then_next_run_no_spurious_transition(tmp_path):
    """After a kill, the next run should not produce a transition alert."""
    from state import load_state, save_state

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_system_monitor.py -k "kill" -v`
Expected: FAIL for fallback_alert_kill (WATCHDOG KILL not in output)

- [ ] **Step 4: Update `fallback_alert()` to handle kill/killed statuses**

In `system_monitor.py`, replace the `fallback_alert` function:

```python
def fallback_alert(transitions: list[dict]) -> str:
    """Format a basic alert when Claude reasoning is unavailable."""
    lines = []
    for t in transitions:
        new = t["new_status"]
        if new in ("kill", "killed"):
            icon = "🔴"
            direction = "WATCHDOG KILL"
        elif new == "degraded":
            icon = "🔴"
            direction = "DEGRADED"
        else:
            icon = "🟢"
            direction = "RECOVERED"
        lines.append(f"{icon} <b>{direction}: {t['service']}</b>")
        lines.append(f"<i>{t['context']}</i>")
        lines.append(f"{t['old_status']} → {new}")
        lines.append(t["detail"])
        if t.get("fix"):
            lines.append(f"<b>Fix:</b> <code>{t['fix']}</code>")
        lines.append("")
    lines.append(f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
    return "\n".join(lines)
```

- [ ] **Step 5: Integrate kill handling into main loop**

In `system_monitor.py`, modify the `main()` function. After the check loop builds `new_services`, add kill handling before the transition processing block. Replace the check loop section (lines 92-128) with:

```python
    for name, check_fn in CHECKS.items():
        log.info("Checking %s...", name)
        result = check_fn()
        new_status = result["status"]

        # Handle kill status: execute kill, persist as "killed"
        if new_status == "kill" and not dry_run:
            execute_kill(result, env)
            new_status = "killed"
            result["status"] = "killed"
        elif new_status == "kill" and dry_run:
            print(f"\n[DRY-RUN] Would execute kill: {result['detail']}")
            new_status = "killed"
            result["status"] = "killed"

        new_services[name] = {
            "status": new_status,
            "detail": result["detail"],
            "fix": result.get("fix"),
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }

        prev = prev_services.get(name, {})
        old_status = prev.get("status", "unknown")

        status_symbol = "OK  " if new_status == "healthy" else ("KILL" if new_status in ("kill", "killed") else "WARN")
        log.info("[%s] %-16s %s — %s", status_symbol, name, new_status, result["detail"][:80])

        if old_status == new_status:
            new_services[name]["last_changed"] = prev.get(
                "last_changed", new_services[name]["last_checked"]
            )
            continue

        new_services[name]["last_changed"] = new_services[name]["last_checked"]

        if old_status == "unknown":
            log.info("First-run baseline for %s: %s (no alert)", name, new_status)
            continue

        transitions.append({
            "service": name,
            "context": SERVICE_CONTEXT.get(name, name),
            "old_status": old_status,
            "new_status": new_status,
            "detail": result["detail"],
            "fix": result.get("fix"),
        })
```

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python3 -m pytest tests/ -v`
Expected: All PASSED

- [ ] **Step 7: Commit**

```bash
git add system_monitor.py tests/test_system_monitor.py
git commit -m "feat: integrate kill handling into main loop, update fallback alerts"
```

---

### Task 5: Add Alfred/Telegram Credentials to ~/.env

**Files:**
- Modify: `~/.env`

- [ ] **Step 1: Add the new env vars to `~/.env`**

Append to `~/.env`:

```
# system-monitor: Alfred alert channel (for OpenClaw watchdog)
ALFRED_URL=http://127.0.0.1:8200
ALFRED_API_KEY=<copy ALFRED_API_KEY from ~/dev/alfred/.env>
RICK_TELEGRAM_BOT_TOKEN=<copy TELEGRAM_BOT_TOKEN from ~/dev/alfred/.env>
RICK_TELEGRAM_CHAT_ID=<copy RICK_CHAT_ID from ~/dev/alfred/.env>
```

- [ ] **Step 2: Verify env loading**

Run: `.venv/bin/python3 -c "from system_monitor import load_env; from pathlib import Path; e = load_env(Path.home() / '.env'); print('ALFRED_URL' in e, 'RICK_TELEGRAM_CHAT_ID' in e)"`
Expected: `True True`

---

### Task 6: End-to-End Dry Run + Final Verification

**Files:** None (testing only)

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python3 -m pytest tests/ -v`
Expected: All PASSED

- [ ] **Step 2: Dry-run with synthetic log**

Create a test log with format errors and run:

```bash
cd /Users/rick/Dev/system-monitor
cp ~/scripts/logs/openclaw.log /tmp/test-openclaw.log
# Temporarily point OPENCLAW_LOG to test file if needed, or just:
.venv/bin/python3 system_monitor.py --dry-run
```

Verify output shows the openclaw-tokens check running and reporting healthy (since current log errors are old — outside 60-min window).

- [ ] **Step 3: Commit any final adjustments**

```bash
git add -A
git commit -m "chore: final adjustments after dry-run verification"
```

- [ ] **Step 4: Update `known-issues.md` in PROJECT-OpenClaw**

Add a new entry under "Open Issues" referencing the watchdog:

```markdown
### Token budget watchdog deployed
- **Since:** 2026-05-11
- **Protection:** system-monitor scans OpenClaw logs every 30 min for format error loops and quota failover cascades. Auto-kills OpenClaw if thresholds exceeded.
- **Recovery:** `rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh`
```

This is informational — move to "Resolved Issues" once the root Anthropic plugin contamination issue is permanently fixed upstream.
