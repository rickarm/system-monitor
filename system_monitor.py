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
import json
import logging
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from checks import CHECKS, SERVICE_CONTEXT, OPENCLAW_KILL_MARKER
from state import load_state, save_state
from telegram import send_telegram
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


def execute_kill(check_result: dict, env: dict) -> None:
    """Execute the full kill mechanism: stop, pkill, marker, alert, issue."""
    reason = check_result["reason"]
    detail = check_result["detail"]
    log_excerpts = check_result.get("log_excerpts", [])

    log.warning("TOKEN WATCHDOG: Killing OpenClaw — %s", detail)

    # Stop and unload
    try:
        subprocess.run(
            [str(HOME / "scripts/stop-openclaw.sh")],
            capture_output=True, text=True, timeout=15,
        )
        log.info("OpenClaw stop script executed")
    except Exception as e:
        log.error("Failed to run stop script: %s", e)

    # Kill orphaned gateway processes
    try:
        subprocess.run(
            ["pkill", "-f", "openclaw-gateway"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass  # pkill returns non-zero if no matches — that's fine

    # Write kill marker
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

    # Alert via Alfred
    send_alfred_alert(
        alfred_url=env.get("ALFRED_URL", "http://127.0.0.1:8200"),
        alfred_api_key=env.get("ALFRED_API_KEY", ""),
        service="openclaw",
        transition="ok->down",
        detail=f"TOKEN WATCHDOG KILL: {detail}. Service unloaded from launchd. Manual restart required: rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh",
        fallback_token=env.get("RICK_TELEGRAM_BOT_TOKEN", ""),
        fallback_chat_id=env.get("RICK_TELEGRAM_CHAT_ID", ""),
    )

    # Create GitHub issue
    create_github_issue(reason, detail, log_excerpts, marker_contents)


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
