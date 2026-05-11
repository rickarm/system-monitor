# System Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a hybrid health monitoring agent that runs deterministic service checks and invokes Claude via the Agent SDK only on state transitions to reason about root cause and compose intelligent Telegram alerts.

**Architecture:** Python script runs 5 deterministic health checks (same as health-monitor). State is persisted to JSON. On status transitions (healthy→degraded or vice versa), the script invokes Claude via `claude-agent-sdk` with Read/Bash tools, passing the check results as context. Claude reasons about root cause, severity, and composes a Telegram alert. The script sends the alert via Telegram Bot API.

**Tech Stack:** Python 3, claude-agent-sdk, launchd, Telegram Bot API (urllib)

---

## File Structure

```
~/Dev/system-monitor/
├── system_monitor.py          # Main script: checks, state, Claude reasoning, Telegram
├── checks.py                  # 5 deterministic health check functions
├── state.py                   # State persistence (load/save JSON)
├── telegram.py                # Telegram send helper
├── reasoning.py               # Claude Agent SDK integration (invoke on transitions)
├── tests/
│   ├── test_checks.py         # Unit tests for each health check
│   ├── test_state.py          # Unit tests for state load/save
│   ├── test_telegram.py       # Unit tests for Telegram formatting
│   └── test_reasoning.py      # Unit tests for reasoning prompt assembly
├── com.rickarmbrust.system-monitor.plist  # launchd agent
├── install.sh                 # One-time installer
├── requirements.txt           # Dependencies
├── .gitignore                 # Standard ignores
├── CLAUDE.md                  # Project instructions
└── README.md                  # User documentation
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `~/Dev/system-monitor/.gitignore`
- Create: `~/Dev/system-monitor/requirements.txt`

- [ ] **Step 1: Initialize git repo**

```bash
cd ~/Dev/system-monitor
git init
```

- [ ] **Step 2: Create .gitignore**

```
.venv/
__pycache__/
*.pyc
*.pyo
.env
*.json
*.log
```

- [ ] **Step 3: Create requirements.txt**

```
claude-agent-sdk
```

- [ ] **Step 4: Create venv and install deps**

```bash
cd ~/Dev/system-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 5: Verify claude-agent-sdk installed**

Run: `.venv/bin/python3 -c "import claude_agent_sdk; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
cd ~/Dev/system-monitor
git add .gitignore requirements.txt
git commit -m "chore: project scaffold with claude-agent-sdk dependency"
```

---

### Task 2: State Persistence Module

**Files:**
- Create: `~/Dev/system-monitor/state.py`
- Create: `~/Dev/system-monitor/tests/test_state.py`

- [ ] **Step 1: Write failing tests for state module**

Create `tests/__init__.py` (empty) and `tests/test_state.py`:

```python
import json
from pathlib import Path
from state import load_state, save_state


def test_load_state_missing_file(tmp_path):
    path = tmp_path / "state.json"
    result = load_state(path)
    assert result == {"services": {}}


def test_load_state_valid_json(tmp_path):
    path = tmp_path / "state.json"
    data = {"services": {"sherlock-hq": {"status": "healthy"}}}
    path.write_text(json.dumps(data))
    result = load_state(path)
    assert result == data


def test_load_state_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bad json")
    result = load_state(path)
    assert result == {"services": {}}


def test_save_state_creates_file(tmp_path):
    path = tmp_path / "subdir" / "state.json"
    data = {"services": {"test": {"status": "degraded"}}, "last_run": "now"}
    save_state(data, path)
    assert path.exists()
    assert json.loads(path.read_text()) == data


def test_save_state_atomic(tmp_path):
    """Verify no .tmp file left behind after save."""
    path = tmp_path / "state.json"
    save_state({"services": {}}, path)
    assert not path.with_suffix(".json.tmp").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'state'`

- [ ] **Step 3: Implement state.py**

```python
"""State persistence for system-monitor."""

import json
from pathlib import Path


def load_state(path: Path) -> dict:
    """Load persisted status from previous run."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"services": {}}


def save_state(state: dict, path: Path) -> None:
    """Atomically persist current state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_state.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd ~/Dev/system-monitor
git add state.py tests/__init__.py tests/test_state.py
git commit -m "feat: state persistence module with atomic writes"
```

---

### Task 3: Health Check Functions

**Files:**
- Create: `~/Dev/system-monitor/checks.py`
- Create: `~/Dev/system-monitor/tests/test_checks.py`

- [ ] **Step 1: Write failing tests for checks**

```python
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


# ── sherlock-hq ──────────────────────────────────────────────────────────

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


# ── sleep-watcher ────────────────────────────────────────────────────────

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


# ── openclaw ─────────────────────────────────────────────────────────────

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


# ── peloton-sync ─────────────────────────────────────────────────────────

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


# ── git-pull-repos ───────────────────────────────────────────────────────

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'checks'`

- [ ] **Step 3: Implement checks.py**

Port the 5 check functions from `~/Dev/health-monitor/health_monitor.py` (lines 102-343) into `checks.py`. Same logic, same log paths, same fix commands. Extract into standalone module with `CHECKS` registry dict.

```python
"""Deterministic health checks for Mac mini services."""

import http.client
import re
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
SLEEP_WATCHER_LOG = HOME / "Library/Logs/sleep-watcher.log"
PELOTON_SYNC_LOG = HOME / "scripts/logs/peloton-sync.log"
GIT_PULL_LOG = HOME / "scripts/logs/git-pull-repos.log"

SERVICE_CONTEXT = {
    "sherlock-hq": "FastAPI dashboard (port 8300)",
    "sleep-watcher": "Oura → Airtable sync daemon",
    "openclaw": "Mandy Telegram bot agent",
    "peloton-sync": "Peloton CSV → Airtable sync",
    "git-pull-repos": "Nightly git pull across all repos",
}


def ok(detail: str) -> dict:
    return {"status": "healthy", "detail": detail}


def degraded(detail: str, fix: str | None = None) -> dict:
    r: dict = {"status": "degraded", "detail": detail}
    if fix:
        r["fix"] = fix
    return r


def check_sherlock_hq() -> dict:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 8300, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        if resp.status == 200:
            return ok(f"HTTP {resp.status}")
        return degraded(
            f"HTTP {resp.status} (expected 200)",
            fix="launchctl kickstart -k gui/$(id -u)/com.rickarmbrust.sherlock-hq",
        )
    except Exception as e:
        return degraded(
            f"Connection failed: {e}",
            fix="launchctl kickstart -k gui/$(id -u)/com.rickarmbrust.sherlock-hq",
        )


def check_sleep_watcher() -> dict:
    try:
        result = subprocess.run(
            ["launchctl", "list", "com.rick.sleep_watcher"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return degraded(
                "com.rick.sleep_watcher not loaded in launchd",
                fix="launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rick.sleep_watcher.plist",
            )
    except Exception as e:
        return degraded(f"launchctl check failed: {e}")

    if not SLEEP_WATCHER_LOG.exists():
        return degraded(
            "Log file not found: ~/Library/Logs/sleep-watcher.log",
            fix="Check if sleep-airtable is writing logs correctly",
        )

    try:
        stat = SLEEP_WATCHER_LOG.stat()
        age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600
        if age_hours > 25:
            return degraded(
                f"Log last modified {age_hours:.1f}h ago (threshold: 25h)",
                fix="launchctl kickstart -k gui/$(id -u)/com.rick.sleep_watcher",
            )

        lines = SLEEP_WATCHER_LOG.read_text(errors="replace").splitlines()
        recent = lines[-100:] if len(lines) > 100 else lines
        consecutive_perm_errors = 0
        for line in reversed(recent):
            if "PermissionError" in line:
                consecutive_perm_errors += 1
            else:
                break
        if consecutive_perm_errors > 2:
            return degraded(
                f"{consecutive_perm_errors} consecutive PermissionErrors in log",
                fix="Check Oura ring connection and TCC permissions",
            )
    except OSError as e:
        return degraded(f"Could not read sleep-watcher log: {e}")

    return ok("Process running, log fresh, no PermissionError streak")


def check_openclaw() -> dict:
    try:
        result = subprocess.run(
            ["launchctl", "list", "com.rickarmbrust.openclaw"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return degraded(
                "com.rickarmbrust.openclaw not loaded in launchd",
                fix="launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rickarmbrust.openclaw.plist",
            )
        if '"PID"' not in result.stdout:
            return degraded(
                "com.rickarmbrust.openclaw loaded but process is not running (no PID)",
                fix="launchctl kickstart -k gui/$(id -u)/com.rickarmbrust.openclaw",
            )
    except Exception as e:
        return degraded(f"launchctl check failed: {e}")
    return ok("Loaded and process running")


def check_peloton_sync() -> dict:
    if not PELOTON_SYNC_LOG.exists():
        return degraded(
            "Log not found: ~/scripts/logs/peloton-sync.log",
            fix="Check if peloton-sync has ever run",
        )
    try:
        lines = PELOTON_SYNC_LOG.read_text(errors="replace").splitlines()
        last_line = ""
        for line in reversed(lines):
            if line.strip():
                last_line = line.strip()
                break
        if not last_line:
            return degraded("Log is empty or has no content")

        ts_match = re.match(r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]", last_line)
        if ts_match:
            try:
                ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                age_days = (datetime.now() - ts).total_seconds() / 86400
                if age_days > 8:
                    return degraded(
                        f"Last sync was {age_days:.1f} days ago (threshold: 8 days). Last line: {last_line[:80]}",
                        fix="Run ~/scripts/peloton-sync.sh manually",
                    )
            except ValueError:
                pass

        success_patterns = ["SUCCESS", "No changes detected"]
        failure_patterns = ["FAILED", "ERROR", "error", "Traceback"]
        is_success = any(p in last_line for p in success_patterns)
        is_failure = any(p in last_line for p in failure_patterns)

        if is_failure and not is_success:
            return degraded(
                f"Last log line indicates failure: {last_line[:120]}",
                fix="Run ~/scripts/peloton-sync.sh manually or check for new CSV",
            )
        if not is_success:
            return degraded(
                f"Last log line doesn't match success pattern: {last_line[:120]}",
                fix="Check ~/scripts/logs/peloton-sync.log",
            )
    except OSError as e:
        return degraded(f"Could not read peloton-sync log: {e}")
    return ok(f"Last sync successful: {last_line[:80]}")


def check_git_pull_repos() -> dict:
    if not GIT_PULL_LOG.exists():
        return degraded(
            "Log not found: ~/scripts/logs/git-pull-repos.log",
            fix="Check com.rickarmbrust.git-pull launchd service",
        )
    try:
        content = GIT_PULL_LOG.read_text(errors="replace")
        run_blocks = re.split(r"=== git-pull-repos: [\d\-: ]+ ===", content)
        if len(run_blocks) < 2:
            return degraded("No completed runs found in git-pull-repos.log")

        last_block = run_blocks[-1]
        done_match = re.search(r"DONE\s+updated=\d+\s+failed=(\d+)\s+skipped=\d+", last_block)
        if not done_match:
            return degraded(
                "Last run has no DONE line — may still be running or crashed",
                fix="Check git-pull-repos launchd service",
            )

        failed_count = int(done_match.group(1))
        if failed_count > 0:
            fail_lines = [l.strip() for l in last_block.splitlines() if "FAIL" in l or "ERROR" in l]
            detail = f"{failed_count} repo(s) failed. " + (fail_lines[0][:80] if fail_lines else "")
            return degraded(detail, fix="cd ~/Dev/<failing-repo> && git pull")

        header_matches = list(re.finditer(r"=== git-pull-repos: ([\d\-: ]+) ===", content))
        last_ts_str = header_matches[-1].group(1).strip() if header_matches else "unknown"
        return ok(f"Last run {last_ts_str}: failed=0")
    except OSError as e:
        return degraded(f"Could not read git-pull-repos log: {e}")


CHECKS = {
    "sherlock-hq": check_sherlock_hq,
    "sleep-watcher": check_sleep_watcher,
    "openclaw": check_openclaw,
    "peloton-sync": check_peloton_sync,
    "git-pull-repos": check_git_pull_repos,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_checks.py -v`
Expected: All 13 tests pass

- [ ] **Step 5: Commit**

```bash
cd ~/Dev/system-monitor
git add checks.py tests/test_checks.py
git commit -m "feat: 5 deterministic health check functions"
```

---

### Task 4: Telegram Module

**Files:**
- Create: `~/Dev/system-monitor/telegram.py`
- Create: `~/Dev/system-monitor/tests/test_telegram.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_telegram.py -v`
Expected: FAIL

- [ ] **Step 3: Implement telegram.py**

```python
"""Telegram Bot API helper for system-monitor."""

import json
import logging
import urllib.request
import urllib.error

log = logging.getLogger(__name__)


def format_degraded_context(transitions: list[dict]) -> str:
    """Format transition data into a structured context string for Claude."""
    lines = []
    for t in transitions:
        direction = "DEGRADED" if t["new_status"] == "degraded" else "RECOVERED"
        lines.append(f"[{direction}] {t['service']} ({t['context']})")
        lines.append(f"  {t['old_status']} -> {t['new_status']}")
        lines.append(f"  Detail: {t['detail']}")
        if t.get("fix"):
            lines.append(f"  Suggested fix: {t['fix']}")
        lines.append("")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return True
            log.error("Telegram API error: %s", data.get("description", "unknown"))
            return False
    except urllib.error.HTTPError as e:
        log.error("Telegram HTTP error %d: %s", e.code, e.read().decode())
        return False
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_telegram.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd ~/Dev/system-monitor
git add telegram.py tests/test_telegram.py
git commit -m "feat: Telegram send helper and context formatter"
```

---

### Task 5: Claude Reasoning Module

**Files:**
- Create: `~/Dev/system-monitor/reasoning.py`
- Create: `~/Dev/system-monitor/tests/test_reasoning.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_reasoning.py -v`
Expected: FAIL

- [ ] **Step 3: Implement reasoning.py**

```python
"""Claude Agent SDK integration for system-monitor.

Invoked only on state transitions to reason about root cause and compose alerts.
"""

import asyncio
import logging
from telegram import format_degraded_context

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Mac mini infrastructure monitor. When services change status, you:
1. Analyze the evidence to determine the likely root cause
2. Assess severity (critical / warning / info)
3. Compose a concise Telegram alert in HTML format

Output ONLY the Telegram message — no preamble, no markdown fences.

Format:
- Use <b>bold</b> for service names and status
- Use <code>monospace</code> for fix commands
- Use <i>italic</i> for context
- Keep it under 500 characters
- Lead with an emoji: 🔴 for degraded, 🟢 for recovered
- Include the fix command if one is suggested
"""


def build_prompt(transitions: list[dict]) -> str:
    """Build the prompt for Claude with transition context."""
    context = format_degraded_context(transitions)
    return f"""\
The following service status transitions were detected on the Mac mini:

{context}

Compose a Telegram alert message. Reason briefly about the likely cause, \
then output the HTML-formatted message to send. If multiple services changed, \
note whether they might be related (e.g., a shared dependency like launchd or network)."""


async def reason_about_transitions(transitions: list[dict]) -> str | None:
    """Invoke Claude via Agent SDK to reason about transitions and compose an alert.

    Returns the composed Telegram message, or None if reasoning fails.
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

        prompt = build_prompt(transitions)
        result_text = None

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=SYSTEM_PROMPT,
                allowed_tools=["Read", "Bash"],
                max_turns=3,
                model="claude-haiku-4-5",
            ),
        ):
            if isinstance(message, ResultMessage):
                result_text = message.result

        return result_text
    except Exception as e:
        log.error("Claude reasoning failed: %s", e)
        return None


def reason_sync(transitions: list[dict]) -> str | None:
    """Synchronous wrapper for reason_about_transitions."""
    return asyncio.run(reason_about_transitions(transitions))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_reasoning.py -v`
Expected: 3 passed (tests only exercise `build_prompt`, not the async SDK call)

- [ ] **Step 5: Commit**

```bash
cd ~/Dev/system-monitor
git add reasoning.py tests/test_reasoning.py
git commit -m "feat: Claude reasoning module with Agent SDK integration"
```

---

### Task 6: Main Script

**Files:**
- Create: `~/Dev/system-monitor/system_monitor.py`

- [ ] **Step 1: Implement system_monitor.py**

```python
#!/usr/bin/env python3
"""
system_monitor.py — Mac mini service health monitor with AI reasoning.

Runs every 30 minutes via launchd. Checks 5 services, detects state
transitions, and invokes Claude (via Agent SDK) to reason about root
cause and compose Telegram alerts.

Usage:
    python3 system_monitor.py            # normal run
    python3 system_monitor.py --dry-run  # print alerts, no Telegram, no Claude
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from checks import CHECKS, SERVICE_CONTEXT
from state import load_state, save_state
from telegram import send_telegram, format_degraded_context
from reasoning import reason_sync

HOME = Path.home()
LOG_FILE = HOME / "scripts/logs/system-monitor.log"
STATE_FILE = HOME / "scripts/logs/system-monitor-state.json"
ENV_FILE = HOME / ".env"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fallback_alert(transitions: list[dict]) -> str:
    """Format a basic alert when Claude reasoning is unavailable."""
    lines = []
    for t in transitions:
        icon = "🔴" if t["new_status"] == "degraded" else "🟢"
        direction = "DEGRADED" if t["new_status"] == "degraded" else "RECOVERED"
        lines.append(f"{icon} <b>{direction}: {t['service']}</b>")
        lines.append(f"<i>{t['context']}</i>")
        lines.append(f"{t['old_status']} → {t['new_status']}")
        lines.append(t["detail"])
        if t.get("fix"):
            lines.append(f"<b>Fix:</b> <code>{t['fix']}</code>")
        lines.append("")
    lines.append(f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
    return "\n".join(lines)


def main(dry_run: bool = False) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info("=== system-monitor: %s%s ===", ts, " [DRY-RUN]" if dry_run else "")

    env = load_env(ENV_FILE)
    telegram_token = env.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = env.get("TELEGRAM_CHAT_ID", "")
    anthropic_key = env.get("ANTHROPIC_API_KEY", "")

    if not dry_run and (not telegram_token or not telegram_chat_id):
        log.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — alerts disabled")

    state = load_state(STATE_FILE)
    prev_services = state.get("services", {})
    new_services: dict = {}
    transitions: list[dict] = []

    for name, check_fn in CHECKS.items():
        log.info("Checking %s...", name)
        result = check_fn()
        new_status = result["status"]
        new_services[name] = {
            "status": new_status,
            "detail": result["detail"],
            "fix": result.get("fix"),
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }

        prev = prev_services.get(name, {})
        old_status = prev.get("status", "unknown")

        status_symbol = "OK  " if new_status == "healthy" else "WARN"
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

    # Process transitions
    if transitions:
        log.info("%d transition(s) detected, composing alert...", len(transitions))

        alert_msg = None
        if not dry_run and anthropic_key:
            log.info("Invoking Claude for reasoning...")
            alert_msg = reason_sync(transitions)
            if alert_msg:
                log.info("Claude composed alert (%d chars)", len(alert_msg))
            else:
                log.warning("Claude reasoning failed, using fallback")

        if not alert_msg:
            alert_msg = fallback_alert(transitions)

        if dry_run:
            print(f"\n{'='*60}")
            print("[DRY-RUN] Would send Telegram alert:")
            print(alert_msg)
            print("=" * 60)
        elif telegram_token and telegram_chat_id:
            if send_telegram(telegram_token, telegram_chat_id, alert_msg):
                log.info("Telegram alert sent for %d transition(s)", len(transitions))
            else:
                log.error("Failed to send Telegram alert")
        else:
            log.warning("Skipping Telegram alert (no credentials)")

    # Save state
    state["services"] = new_services
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state, STATE_FILE)

    healthy = sum(1 for s in new_services.values() if s["status"] == "healthy")
    total = len(new_services)
    log.info("Done: %d/%d healthy, %d transition(s)", healthy, total, len(transitions))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mac mini service health monitor with AI reasoning")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts without sending")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
```

- [ ] **Step 2: Verify syntax**

Run: `.venv/bin/python3 -m py_compile system_monitor.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
cd ~/Dev/system-monitor
git add system_monitor.py
git commit -m "feat: main system-monitor script with hybrid check + reason architecture"
```

---

### Task 7: Launchd Plist and Install Script

**Files:**
- Create: `~/Dev/system-monitor/com.rickarmbrust.system-monitor.plist`
- Create: `~/Dev/system-monitor/install.sh`

- [ ] **Step 1: Create plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rickarmbrust.system-monitor</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/rick/Dev/system-monitor/.venv/bin/python3</string>
        <string>/Users/rick/Dev/system-monitor/system_monitor.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/rick/Dev/system-monitor</string>

    <!-- Run every 30 minutes -->
    <key>StartInterval</key>
    <integer>1800</integer>

    <!-- Also run once at login/load -->
    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/rick/scripts/logs/system-monitor-launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/rick/scripts/logs/system-monitor-launchd.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/rick</string>
    </dict>
</dict>
</plist>
```

- [ ] **Step 2: Create install.sh**

```bash
#!/bin/bash
# install.sh — installs and loads the system-monitor launchd agent
#
# Run this ONCE after cloning/setting up the project.
# Prerequisites:
#   1. Python venv created: python3 -m venv .venv
#   2. Dependencies installed: .venv/bin/pip install -r requirements.txt
#   3. ~/.env contains TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY
#
# Usage: bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.rickarmbrust.system-monitor.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.rickarmbrust.system-monitor.plist"
LABEL="com.rickarmbrust.system-monitor"

echo "=== system-monitor install ==="

# 1. Python venv
if [ ! -f "$SCRIPT_DIR/.venv/bin/python3" ]; then
    echo "ERROR: .venv not found."
    echo "  Run: python3 -m venv $SCRIPT_DIR/.venv"
    exit 1
fi
echo "✓ Python venv found"

# 2. claude-agent-sdk installed
if ! "$SCRIPT_DIR/.venv/bin/python3" -c "import claude_agent_sdk" 2>/dev/null; then
    echo "ERROR: claude-agent-sdk not installed."
    echo "  Run: $SCRIPT_DIR/.venv/bin/pip install -r $SCRIPT_DIR/requirements.txt"
    exit 1
fi
echo "✓ claude-agent-sdk installed"

# 3. ~/.env credentials
if [ -f "$HOME/.env" ]; then
    missing=""
    grep -q "TELEGRAM_BOT_TOKEN" "$HOME/.env" || missing="TELEGRAM_BOT_TOKEN "
    grep -q "TELEGRAM_CHAT_ID" "$HOME/.env" || missing="${missing}TELEGRAM_CHAT_ID "
    grep -q "ANTHROPIC_API_KEY" "$HOME/.env" || missing="${missing}ANTHROPIC_API_KEY"
    if [ -z "$missing" ]; then
        echo "✓ ~/.env has all required credentials"
    else
        echo "WARNING: ~/.env may be missing: $missing"
        echo "  Monitor will run but some features may be degraded."
    fi
else
    echo "WARNING: ~/.env not found — alerts and AI reasoning will be disabled."
fi

# 4. Smoke test
echo "Running syntax check..."
"$SCRIPT_DIR/.venv/bin/python3" -m py_compile "$SCRIPT_DIR/system_monitor.py"
echo "✓ Syntax OK"

# ── Install plist ─────────────────────────────────────────────────────────

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

cp "$PLIST_SRC" "$PLIST_DEST"
echo "✓ Copied plist to ~/Library/LaunchAgents/"

launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl kickstart "gui/$(id -u)/$LABEL"

echo ""
echo "=== Done ==="
echo "Agent loaded as: $LABEL"
echo "Runs every 30 minutes. Logs at:"
echo "  ~/scripts/logs/system-monitor.log          (script log)"
echo "  ~/scripts/logs/system-monitor-launchd.log  (launchd stdout/stderr)"
echo "  ~/scripts/logs/system-monitor-state.json   (current state)"
echo ""
echo "Verify with:"
echo "  launchctl list | grep system-monitor"
echo "  tail -f ~/scripts/logs/system-monitor.log"
```

- [ ] **Step 3: Commit**

```bash
cd ~/Dev/system-monitor
git add com.rickarmbrust.system-monitor.plist install.sh
git commit -m "feat: launchd plist and install script"
```

---

### Task 8: CLAUDE.md and README

**Files:**
- Create: `~/Dev/system-monitor/CLAUDE.md`
- Create: `~/Dev/system-monitor/README.md`

- [ ] **Step 1: Create CLAUDE.md**

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Mac mini service health monitor with AI-powered reasoning. Hybrid architecture: deterministic Python checks run every 30 minutes, Claude (via Agent SDK) is invoked only on state transitions to reason about root cause and compose Telegram alerts.

## Development Workflow

See `KB-Development-Workflow.md` in the Knowledge Base: GitHub Issues with `claude` label trigger Claude Code via GitHub Actions.

## Commands

```bash
# Run
.venv/bin/python3 system_monitor.py              # live (sends Telegram + invokes Claude)
.venv/bin/python3 system_monitor.py --dry-run     # preview (no Telegram, no Claude, prints alerts)

# Test
.venv/bin/python3 -m pytest tests/ -v             # all tests
.venv/bin/python3 -m pytest tests/test_checks.py  # just health checks

# Setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash install.sh                                   # install launchd agent (don't run until ready)
```

## Architecture

- `checks.py` — 5 deterministic health checks (ported from health-monitor, same logic)
- `state.py` — JSON state persistence with atomic writes
- `telegram.py` — Telegram Bot API send + context formatting
- `reasoning.py` — Claude Agent SDK integration (only invoked on transitions)
- `system_monitor.py` — Main script: runs checks, detects transitions, orchestrates reasoning + alerting

## How Claude Is Used

Claude is invoked via `claude-agent-sdk` with `model="claude-haiku-4-5"` only when a service transitions between healthy/degraded. It receives the check results and composes an HTML-formatted Telegram alert with root cause analysis. If Claude fails (network, API error, missing key), a deterministic fallback alert is sent instead.

## Credentials

Reads from `~/.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`

## Gotchas

- Claude is only called on transitions, not every cycle — keeps API costs near zero
- If ANTHROPIC_API_KEY is missing, falls back to basic alerts (same as health-monitor)
- State file at `~/scripts/logs/system-monitor-state.json` (separate from health-monitor's state)
- Logs to `~/scripts/logs/system-monitor.log` (separate from health-monitor)
- This project uses claude-agent-sdk (requires Claude Code CLI installed) — not the raw anthropic SDK
- First run establishes baseline without alerts (unknown → any status is silent)
```

- [ ] **Step 2: Create README.md**

```markdown
# system-monitor

Mac mini service health monitor with AI-powered reasoning via Claude Agent SDK.

## How It Works

1. **Deterministic checks** run every 30 minutes via launchd
2. **State tracking** persists service status to JSON — alerts only on transitions
3. **Claude reasoning** (via Agent SDK) is invoked only when status changes, composing intelligent alerts
4. **Telegram alerts** sent via Mandy bot with root cause analysis and fix commands

## Services Monitored

| Service | Check Method |
|---------|-------------|
| sherlock-hq | HTTP GET localhost:8300/health |
| sleep-watcher | launchd process + log freshness + PermissionError detection |
| openclaw | launchd service loaded + PID alive |
| peloton-sync | Last log entry is success + <8 days old |
| git-pull-repos | Last run had failed=0 |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Test
.venv/bin/python3 system_monitor.py --dry-run

# Install (loads launchd agent)
bash install.sh
```

## Credentials

Add to `~/.env`:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ANTHROPIC_API_KEY=...
```
```

- [ ] **Step 3: Commit**

```bash
cd ~/Dev/system-monitor
git add CLAUDE.md README.md
git commit -m "docs: CLAUDE.md and README"
```

---

### Task 9: Dry-Run Verification

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/python3 -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run dry-run**

Run: `.venv/bin/python3 system_monitor.py --dry-run`
Expected: Checks all 5 services, prints status for each, shows any transition alerts in dry-run format. No Telegram sent, no Claude invoked.

- [ ] **Step 3: Verify state file created**

Run: `cat ~/scripts/logs/system-monitor-state.json | python3 -m json.tool`
Expected: JSON with 5 services, each with status/detail/last_checked

- [ ] **Step 4: Final commit if any fixes needed**

```bash
cd ~/Dev/system-monitor
git add -A
git status  # verify nothing unexpected
git commit -m "fix: any dry-run fixes" --allow-empty
```
