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
