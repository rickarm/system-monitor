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
