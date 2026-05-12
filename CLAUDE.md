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

- `checks.py` — 6 health checks: 5 service checks + OpenClaw token watchdog
- `state.py` — JSON state persistence with atomic writes
- `telegram.py` — Telegram Bot API send + context formatting
- `reasoning.py` — Claude Agent SDK integration (only invoked on transitions)
- `system_monitor.py` — Main script: runs checks, detects transitions, orchestrates reasoning + alerting

## OpenClaw Token Watchdog

`check_openclaw_token_health()` scans `~/scripts/logs/openclaw.log` for token-burning error loops. Two kill patterns:
- **Format error retry loop** (6+ gpt-5.4-pro format errors in 60 min) — session contamination
- **Quota failover cascade** (4+ quota-triggered failovers to pro in 60 min) — budget burn

On detection: stops OpenClaw, kills orphaned gateways, writes `~/scripts/logs/openclaw-killed.marker`, alerts via Alfred (`POST /alert`), creates GitHub issue. OpenClaw stays dead until manual restart.

Recovery: `rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh`

Status lifecycle: `healthy` → `kill` (transient, triggers kill) → `killed` (stable, persisted). The `openclaw-tokens` check runs BEFORE `openclaw` in CHECKS dict to ensure consistent state.

## How Claude Is Used

Claude is invoked via `claude-agent-sdk` with `model="claude-haiku-4-5"` only when a service transitions between healthy/degraded. It receives the check results and composes an HTML-formatted Telegram alert with root cause analysis. If Claude fails (network, API error, missing key), a deterministic fallback alert is sent instead.

## Credentials

Reads from `~/.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`, `ALFRED_URL`, `ALFRED_API_KEY`, `RICK_TELEGRAM_BOT_TOKEN`, `RICK_TELEGRAM_CHAT_ID`

## Gotchas

- Claude is only called on transitions, not every cycle — keeps API costs near zero
- If ANTHROPIC_API_KEY is missing, falls back to basic alerts (same as health-monitor)
- State file at `~/scripts/logs/system-monitor-state.json` (separate from health-monitor's state)
- Logs to `~/scripts/logs/system-monitor.log` (separate from health-monitor)
- This project uses claude-agent-sdk (requires Claude Code CLI installed) — not the raw anthropic SDK
- First run establishes baseline without alerts (unknown -> any status is silent)
- Kill marker file (`~/scripts/logs/openclaw-killed.marker`) prevents OpenClaw resurrection by watchdog and things-mcp-restart. Must be manually removed before restarting.
- `openclaw-tokens` must be first in CHECKS dict (before `openclaw`) — ordering matters for consistent state after a kill
- Alfred alerts go to @rick_things_bot (Rick's personal Telegram), independent of Mandy — works even when OpenClaw is the broken service
- Spec and plan docs in `docs/superpowers/specs/` and `docs/superpowers/plans/`
