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
RE_FORMAT_ERROR = re.compile(r"embedded run agent end.*isError=true.*gpt-5\.4-pro.*reasoning.*item")
RE_QUOTA_FAILOVER = re.compile(r"candidate_failed.*reason=rate_limit.*next=openai/gpt-5\.4-pro")

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
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
            }
        except (json.JSONDecodeError, OSError):
            return {
                "status": "killed",
                "detail": "Kill marker present but unreadable",
                "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
            }
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
            fail_lines = [line.strip() for line in last_block.splitlines() if "FAIL" in line or "ERROR" in line]
            detail = f"{failed_count} repo(s) failed. " + (fail_lines[0][:80] if fail_lines else "")
            return degraded(detail, fix="cd ~/Dev/<failing-repo> && git pull")

        header_matches = list(re.finditer(r"=== git-pull-repos: ([\d\-: ]+) ===", content))
        last_ts_str = header_matches[-1].group(1).strip() if header_matches else "unknown"
        return ok(f"Last run {last_ts_str}: failed=0")
    except OSError as e:
        return degraded(f"Could not read git-pull-repos log: {e}")


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

    format_errors = [line for line in recent if RE_FORMAT_ERROR.search(line)]
    quota_failovers = [line for line in recent if RE_QUOTA_FAILOVER.search(line)]

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


CHECKS = {
    "openclaw-tokens": check_openclaw_token_health,
    "sherlock-hq": check_sherlock_hq,
    "sleep-watcher": check_sleep_watcher,
    "openclaw": check_openclaw,
    "peloton-sync": check_peloton_sync,
    "git-pull-repos": check_git_pull_repos,
}
