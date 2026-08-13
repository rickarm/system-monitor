"""Deterministic health checks for Mac mini services."""

import http.client
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

HOME = Path.home()
SLEEP_WATCHER_LOG = HOME / "Library/Logs/sleep-watcher.log"
PELOTON_SYNC_LOG = HOME / "scripts/logs/peloton-sync.log"
GIT_PULL_LOG = HOME / "scripts/logs/git-pull-repos.log"
OPENCLAW_LOG = HOME / "scripts/logs/openclaw.log"
OPENCLAW_KILL_MARKER = HOME / "scripts/logs/openclaw-killed.marker"

OPENCLAW_FORMAT_ERROR_THRESHOLD = 6
OPENCLAW_QUOTA_FAILOVER_THRESHOLD = 4
OPENCLAW_SCAN_WINDOW_MINUTES = 60

RE_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+([+-]\d{2}:\d{2})")
# The gateway log gained a second line format on 2026-08-11 when `logging.file` was pointed
# at it (native structured JSON sink, so the log survives a launchd service swap). Those
# lines start with `{"0":...` and carry their timestamp in a "time" field, so the anchored
# regex above cannot see them. Without this, _parse_ts returns None for every modern line,
# _read_recent_lines never breaks out of its loop, and the "last 60 minutes" window silently
# becomes "the entire file" — turning any historical match into a present-tense kill signal.
# `\s*` after the colon is deliberate. The gateway emits compact JSON (`"time":"..."`), but
# any re-serialisation of a log line through a default json.dumps produces `"time": "..."`
# with a space, and a watchdog that silently stops parsing on a whitespace change is the
# whole failure mode this rewrite exists to prevent.
RE_JSON_TIMESTAMP = re.compile(r'"time":\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+([+-]\d{2}:\d{2})"')

# Poison-pill loop: the agent retries a request the provider keeps rejecting, burning tokens
# with no output. This is the real "runaway" signal and the only one worth killing over.
# Deliberately MODEL-AGNOSTIC. The previous pattern hardcoded `gpt-5.4-pro`, which Mandy
# stopped emitting when she moved to Anthropic on 2026-06-26, so the watchdog could not
# match anything and was inert for ~6 weeks. Do not reintroduce a model name here.
# Sample: embedded run agent end: runId=... isError=true model=claude-sonnet-4-6
#         provider=anthropic error=LLM request failed: provider rejected the request schema
#         or tool payload
RE_POISON_LOOP = re.compile(
    r"embedded run agent end.*isError=true.*provider rejected the request schema"
)

# Provider refusing us because the configured Console spend cap is reached. ALERT ONLY,
# never kill: the provider has already stopped serving requests, so spend has stopped too
# and killing Mandy saves nothing while costing Rick his assistant. There is also no
# mini-side fix — raise the cap in the Anthropic Console or wait for the stated reset.
# Sample: candidate_failed requested=anthropic/claude-sonnet-4-6 ... reason=rate_limit
#         providerErrorType=invalid_request_error next=none detail=You have reached your
#         specified API usage limits.
RE_USAGE_CAP = re.compile(r"reason=rate_limit.*providerErrorType=invalid_request_error")

# NOTE on the retired quota-failover check: it looked for cascading failover to a fallback
# model (`next=openai/gpt-5.4-pro`). Mandy now runs with NO fallbacks configured, so real
# log lines read `next=none` and a failover cascade is structurally impossible. Replaced by
# RE_USAGE_CAP above rather than re-pointed. `reason=overloaded` is deliberately NOT matched
# by anything here: it is the transient provider error and self-resolves.

SERVICE_CONTEXT = {
    "openclaw-tokens": "OpenClaw token budget watchdog",
    "sherlock-hq": "FastAPI dashboard (port 8300)",
    "sleep-watcher": "Oura / Airtable sync daemon",
    "openclaw": "Mandy Telegram bot agent",
    "peloton-sync": "Peloton CSV / Airtable sync",
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
    # If watchdog killed OpenClaw, report killed status
    if OPENCLAW_KILL_MARKER.exists():
        try:
            marker = json.loads(OPENCLAW_KILL_MARKER.read_text())
            reason = marker.get("reason", "unknown")
            killed_at = marker.get("killed_at", "unknown")
            return {
                "status": "killed",
                "detail": f"Watchdog killed at {killed_at} — {reason}",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/Dev/openclaw-config/scripts/start-openclaw.sh",
            }
        except (json.JSONDecodeError, OSError):
            return {
                "status": "killed",
                "detail": "Kill marker present but unreadable",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/Dev/openclaw-config/scripts/start-openclaw.sh",
            }
    # Mandy's gateway is supervised by ONE of these two launchd labels, and which one
    # changes with the A2 migration (our custom plist -> OpenClaw's native service).
    # Checking both, and accepting whichever is loaded, keeps this monitor correct on
    # either side of that flip with no coordinated deploy. A hardcoded label would fail
    # silently: `launchctl list <dead-label>` simply returns non-zero, which this function
    # reports as "not loaded" — i.e. a healthy Mandy would be alarmed as down, or worse,
    # a real outage could be masked once the labels no longer line up.
    labels = ["com.rickarmbrust.openclaw", "ai.openclaw.gateway"]
    loaded_without_pid = []
    try:
        for label in labels:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                continue
            if '"PID"' in result.stdout:
                return ok(f"Loaded and process running ({label})")
            loaded_without_pid.append(label)

        if loaded_without_pid:
            label = loaded_without_pid[0]
            return degraded(
                f"{label} loaded but process is not running (no PID)",
                fix=f"launchctl kickstart -k gui/$(id -u)/{label}",
            )
        return degraded(
            "no OpenClaw gateway service loaded in launchd (checked: "
            + ", ".join(labels) + ")",
            fix="~/Dev/openclaw-config/scripts/start-openclaw.sh",
        )
    except Exception as e:
        return degraded(f"launchctl check failed: {e}")


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
            fail_lines = [line.strip() for line in last_block.splitlines() if "FAIL" in line or "ERROR" in line]
            detail = f"{failed_count} repo(s) failed. " + (fail_lines[0][:80] if fail_lines else "")
            return degraded(detail, fix="cd ~/Dev/<failing-repo> && git pull")

        header_matches = list(re.finditer(r"=== git-pull-repos: ([\d\-: ]+) ===", content))
        last_ts_str = header_matches[-1].group(1).strip() if header_matches else "unknown"
        return ok(f"Last run {last_ts_str}: failed=0")
    except OSError as e:
        return degraded(f"Could not read git-pull-repos log: {e}")


def _parse_ts(line: str) -> datetime | None:
    """Timestamp from either gateway log format: legacy console, or the JSON file sink."""
    m = RE_TIMESTAMP.match(line) or RE_JSON_TIMESTAMP.search(line)
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
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/Dev/openclaw-config/scripts/start-openclaw.sh",
            }
        except (json.JSONDecodeError, OSError):
            return {
                "status": "killed",
                "detail": "Kill marker present but unreadable",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/Dev/openclaw-config/scripts/start-openclaw.sh",
            }

    recent = _read_recent_lines(OPENCLAW_LOG, OPENCLAW_SCAN_WINDOW_MINUTES)

    # Self-check. If the log has content but NOTHING in the window carries a parseable
    # timestamp, the format has drifted from what _parse_ts understands. That is not a
    # cosmetic problem: the window degrades to "the whole file", so a long-past incident
    # would be counted as happening now and could trigger a spurious kill. This exact
    # breakage happened on 2026-08-11 when the log gained JSON lines, and it was invisible
    # because a watchdog that matches nothing looks identical to a healthy system.
    if recent and not any(_parse_ts(line) is not None for line in recent):
        return degraded(
            "Cannot parse timestamps in openclaw.log — the scan window is unbounded, so "
            "kill thresholds are unreliable. Log format likely changed.",
            fix="Update RE_TIMESTAMP / RE_JSON_TIMESTAMP in checks.py to match the current log format",
        )

    poison_loops = [line for line in recent if RE_POISON_LOOP.search(line)]
    usage_caps = [line for line in recent if RE_USAGE_CAP.search(line)]

    counts = {
        "poison_loop": len(poison_loops),
        "usage_cap": len(usage_caps),
    }

    if len(poison_loops) >= OPENCLAW_FORMAT_ERROR_THRESHOLD:
        return {
            "status": "kill",
            "detail": f"{len(poison_loops)} schema-rejection retries in {OPENCLAW_SCAN_WINDOW_MINUTES} min (poison-pill loop burning tokens)",
            "reason": "poison_loop",
            "pattern_counts": counts,
            "log_excerpts": poison_loops[-10:],
        }

    # Deliberately degraded, NOT kill. The provider is already refusing requests, so spend
    # has stopped on its own; killing Mandy would cost Rick his assistant and save nothing.
    if len(usage_caps) >= OPENCLAW_QUOTA_FAILOVER_THRESHOLD:
        return degraded(
            f"{len(usage_caps)} provider refusals in {OPENCLAW_SCAN_WINDOW_MINUTES} min — "
            "Anthropic Console spend cap reached. Spend has already stopped.",
            fix="Raise the cap in Anthropic Console (Settings > Limits), or wait for the reset stated in the log line",
        )

    return ok(f"{counts['poison_loop']} poison-loop retries, {counts['usage_cap']} provider refusals in last {OPENCLAW_SCAN_WINDOW_MINUTES} min")


CHECKS = {
    "openclaw-tokens": check_openclaw_token_health,
    "sherlock-hq": check_sherlock_hq,
    "sleep-watcher": check_sleep_watcher,
    "openclaw": check_openclaw,
    "peloton-sync": check_peloton_sync,
    "git-pull-repos": check_git_pull_repos,
}
