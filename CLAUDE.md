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
- First run establishes baseline without alerts (unknown -> any status is silent)
