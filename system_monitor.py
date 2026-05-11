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
