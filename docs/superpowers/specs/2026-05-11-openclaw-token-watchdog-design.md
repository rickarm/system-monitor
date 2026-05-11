# OpenClaw Token Watchdog — Design Spec

## Problem

On May 8, 2026, OpenClaw's heartbeat failover loop burned 309K input tokens on gpt-5.4-pro in ~6 hours due to cross-provider session contamination. Anthropic `msg_`/`rs_` reasoning items in the session caused every OpenAI request to fail with a format error. OpenClaw retried each failed call 4x with exponential backoff, and the heartbeat fired every 30 minutes — producing 76 failed gpt-5.4-pro calls that exhausted the monthly OpenAI budget. OpenAI charges for input tokens even on 400 errors.

OpenClaw has no built-in circuit breaker for format errors at the agent/inference level. The existing `check_openclaw()` in system-monitor only checks process health (loaded + PID), not operational health.

## Solution

Add an OpenClaw token watchdog check to system-monitor that:

1. Scans `~/scripts/logs/openclaw.log` for token-burning error patterns
2. Autonomously kills OpenClaw (stop + unload plist) when patterns are detected
3. Alerts Rick via Alfred's `/alert` endpoint (independent of Mandy)
4. Creates a GitHub issue for investigation
5. Keeps OpenClaw dead until manual restart

## Detection Patterns

### Kill Pattern 1: Format Error Retry Loop

**What it looks like in logs:**
```
[agent/embedded] embedded run agent end: runId=... isError=true model=gpt-5.4-pro ... rawError=400 Item 'msg_...' of type 'message' was provided without its required 'reasoning' item
```

**Detection:** Count lines matching `embedded run agent end.*isError=true.*gpt-5.4-pro.*reasoning.*item` in the last 60 minutes of log entries.

**Threshold:** 6+ matches in 60 minutes triggers kill.

**Rationale:** The May 8 incident produced 76 such calls. Normal operation produces zero. 6 means at least 2 heartbeat cycles have failed with the same contamination — this will never self-resolve.

### Kill Pattern 2: Quota Failover Cascade

**What it looks like in logs:**
```
[model-fallback/decision] model fallback decision: decision=candidate_failed ... reason=rate_limit next=openai/gpt-5.4-pro
```

**Detection:** Count lines matching `candidate_failed.*reason=rate_limit.*next=openai/gpt-5.4-pro` in the last 60 minutes.

**Threshold:** 4+ matches in 60 minutes triggers kill.

**Rationale:** Quota errors on the cheap model (nano) triggering failover to the expensive model (pro) is the exact pattern that burned budget. 4 occurrences = 2 hours of this loop running, which is already wasteful. Normal operation produces zero.

## Kill Mechanism

When either pattern triggers:

### Step 1: Stop and unload OpenClaw
```bash
~/scripts/stop-openclaw.sh
```

This script already calls `launchctl bootout`, which both stops the process and unloads the plist from the current launchd session. No separate unload step is needed.

### Step 1b: Kill orphaned gateway processes
```bash
pkill -f openclaw-gateway 2>/dev/null || true
```

Per CLAUDE.md, orphaned `openclaw-gateway` processes can persist after `bootout` and continue making API calls. This ensures no process survives the kill.

### Resurrection prevention

After `bootout`, the plist is unloaded from the current launchd session:
- **things-mcp watchdog** (every 2 min): calls `launchctl kickstart` on OpenClaw — this fails silently on an unloaded service (exit code 3, "Could not find service"). Safe.
- **4:00 AM daily restart** (`com.rickarmbrust.things-mcp-restart`): also uses `launchctl kickstart`. Same behavior — fails silently. Safe.
- **System reboot**: plists in `~/Library/LaunchAgents/` with `RunAtLoad: true` auto-load on login. The plist file is still on disk, so OpenClaw WILL restart after reboot. The kill marker file is the guard — `check_openclaw_token_health()` will detect the marker and re-kill if the error patterns are still present in the log. Additionally, a reboot clears the contaminated session (nightly reset runs at 3:55 AM), so the root cause is likely resolved.

### Step 3: Write kill marker
Write `~/scripts/logs/openclaw-killed.marker` as JSON:
```json
{
  "killed_at": "2026-05-08T14:40:00+00:00",
  "reason": "format_error_loop",
  "detail": "8 gpt-5.4-pro format error retries in 60 min",
  "pattern_counts": {"format_error_loop": 8, "quota_failover_cascade": 3}
}
```

### Step 4: Alert via Alfred
POST to `http://127.0.0.1:8200/alert` with:
```json
{
  "service": "openclaw",
  "transition": "ok->down",
  "detail": "TOKEN WATCHDOG KILL: OpenClaw killed and unloaded. 8 gpt-5.4-pro format error retries detected in 60 min (session contamination). Service unloaded from launchd to prevent resurrection. Manual restart required: ~/scripts/start-openclaw.sh"
}
```

If Alfred is unreachable, fall back to direct Telegram Bot API call using credentials from `~/.env` (same as existing `send_telegram()` in `telegram.py`). The bot token and chat ID used should be the Alfred/rick_things_bot credentials from Alfred's `.env`, NOT the system-monitor's `TELEGRAM_CHAT_ID` (which may point to Mandy's chat).

### Step 5: Create GitHub issue
```bash
gh issue create --repo rickarm/system-monitor \
  --title "OpenClaw token watchdog kill: format_error_loop" \
  --body "..."
```

Body includes: timestamp, pattern detected, match count, log excerpts (last 10 matching lines), and the kill marker contents.

If `gh` fails (not installed, auth expired), log a warning and continue — the alert is more important than the issue.

## Guard: Existing check_openclaw() Integration

The existing `check_openclaw()` checks process health. After a watchdog kill, it would see OpenClaw as "not loaded" and report degraded every 30 minutes, generating repeated alerts.

**Fix:** Modify `check_openclaw()` to check for the kill marker file first. If present:
- Return status `"killed"` (new status value) instead of `"degraded"`
- Include the kill reason in the detail
- Set `fix` to `"rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh"`
- This status won't transition (killed → killed), so no repeated alerts

## Manual Recovery

```bash
rm ~/scripts/logs/openclaw-killed.marker
~/scripts/start-openclaw.sh
```

The start script calls `launchctl load -w` which re-registers the plist. Removing the marker lets `check_openclaw()` resume normal process health monitoring.

## New Files

### `checks.py` — modifications

1. Add `OPENCLAW_LOG` path constant: `HOME / "scripts/logs/openclaw.log"`
2. Add `OPENCLAW_KILL_MARKER` path constant: `HOME / "scripts/logs/openclaw-killed.marker"`
3. Add `check_openclaw_token_health()` function — the log scanner. Returns `"killed"` if marker exists, `"kill"` if patterns detected, `"healthy"` otherwise.
4. Modify `check_openclaw()` to check kill marker before process health
5. Add `"openclaw-tokens"` to `CHECKS` dict with context `"OpenClaw token budget watchdog"`
6. Add `"openclaw-tokens"` to `SERVICE_CONTEXT` dict

**Check ordering:** `"openclaw-tokens"` MUST appear before `"openclaw"` in the `CHECKS` dict. This ensures the token check runs first. If it triggers a kill, `check_openclaw()` runs second and sees the marker, returning `"killed"` — consistent state. (Python dicts preserve insertion order since 3.7.)

### `system_monitor.py` — modifications

1. Add kill action handling: when `check_openclaw_token_health()` returns `"kill"` status, execute the kill mechanism (stop, pkill, marker, alert, issue). After executing, persist `"killed"` (not `"kill"`) as the state for this service.
2. Add `send_alfred_alert()` function (HTTP POST to Alfred, fallback to direct Telegram)
3. Add `create_github_issue()` function (subprocess call to `gh`)
4. Update `fallback_alert()` to handle `"kill"` and `"killed"` statuses — use red circle with "WATCHDOG KILL" label instead of the generic degraded/recovered labels
5. Guard kill actions with `--dry-run` flag — in dry-run mode, print what would happen but do not stop/unload/write marker

### `telegram.py` — no changes

Existing `send_telegram()` used as fallback if Alfred is down.

## Check Return Value

`check_openclaw_token_health()` returns a dict with an extended schema:

```python
# Normal (no patterns detected)
{"status": "healthy", "detail": "0 format errors, 0 quota failovers in last 60 min"}

# Kill marker present (already killed by a previous run)
{"status": "killed", "detail": "Watchdog killed OpenClaw at 2026-05-08T14:40:00 — format_error_loop",
 "fix": "rm ~/scripts/logs/openclaw-killed.marker && ~/scripts/start-openclaw.sh"}

# Kill condition (active — triggers kill mechanism)
{
    "status": "kill",
    "detail": "8 gpt-5.4-pro format error retries in 60 min",
    "reason": "format_error_loop",
    "pattern_counts": {"format_error_loop": 8, "quota_failover_cascade": 3},
    "log_excerpts": ["2026-05-08T14:40:30 ...", ...]
}
```

### Status lifecycle

- `"healthy"` — no error patterns detected, no kill marker present
- `"kill"` — active kill condition detected, triggers kill mechanism as a side effect
- `"killed"` — kill marker exists from a previous run; stable state, no action needed

The `"kill"` status is transient. After the kill mechanism executes (stop, marker, alert, issue), the main loop persists `"killed"` as the state for this service — NOT `"kill"`. This prevents a spurious `kill → healthy` recovery transition on the next run when the marker is detected.

On the next run, `check_openclaw_token_health()` sees the marker and returns `"killed"`. State goes `killed → killed` — no transition, no alert.

## Log Parsing Strategy

1. Read `~/scripts/logs/openclaw.log` (the persistent log, not `/tmp/openclaw/` daily logs)
2. Parse timestamps from log lines: `^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[-+]\d{2}:\d{2})`
3. Filter to lines within the last 60 minutes
4. Apply regex patterns to count matches
5. The log file can be large (50K+ lines) — read from the end backwards, stopping once timestamps exceed the 60-minute window

### Efficiency

Read the file in reverse (seek to end, read backwards in chunks) to avoid parsing the entire log. Most runs will examine only ~200 lines (30 min of activity). This keeps the check fast even as the log grows.

## Configuration

Thresholds are constants in `checks.py`, not config-file driven. They're tight enough that any match is abnormal — no need for per-deployment tuning.

```python
OPENCLAW_FORMAT_ERROR_THRESHOLD = 6    # kills in 60 min
OPENCLAW_QUOTA_FAILOVER_THRESHOLD = 4  # kills in 60 min
OPENCLAW_SCAN_WINDOW_MINUTES = 60
```

## Alert Credentials

All credentials read from a single source: `~/.env`. Add these env vars:

```
ALFRED_URL=http://127.0.0.1:8200
ALFRED_API_KEY=<same key as in alfred/.env>
RICK_TELEGRAM_BOT_TOKEN=<alfred bot token, for fallback>
RICK_TELEGRAM_CHAT_ID=<rick's personal chat ID, for fallback>
```

**Primary path:** POST to Alfred's `/alert` endpoint using `ALFRED_URL` and `ALFRED_API_KEY`.

**Fallback path (Alfred unreachable):** Send directly via Telegram Bot API using `RICK_TELEGRAM_BOT_TOKEN` and `RICK_TELEGRAM_CHAT_ID`. These are the same values as in `~/dev/alfred/.env` (`TELEGRAM_BOT_TOKEN` and `RICK_CHAT_ID`), duplicated into `~/.env` under distinct names to avoid confusion with system-monitor's own `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` which may point to a different bot/chat.

## Test Plan

### Unit tests (in `tests/`)

1. **test_check_openclaw_token_health_clean** — no error patterns in log → healthy
2. **test_check_openclaw_token_health_format_errors** — 6+ format error lines → kill with reason `format_error_loop`
3. **test_check_openclaw_token_health_quota_failover** — 4+ quota failover lines → kill with reason `quota_failover_cascade`
4. **test_check_openclaw_token_health_below_threshold** — 3 format errors (below 6) → healthy
5. **test_check_openclaw_token_health_old_errors** — errors older than 60 min → healthy (window filtering works)
6. **test_check_openclaw_token_health_no_log** — missing log file → healthy (not degraded; absence of log is not a token issue)
7. **test_check_openclaw_killed_marker** — kill marker present → `check_openclaw()` returns `"killed"` status
8. **test_kill_mechanism_stop_and_pkill** — mock subprocess calls, verify stop script + pkill called
9. **test_alfred_alert_success** — mock HTTP, verify POST to Alfred
10. **test_alfred_alert_fallback** — Alfred unreachable, verify fallback to direct Telegram
11. **test_github_issue_creation** — mock subprocess, verify `gh issue create` args
12. **test_github_issue_failure_nonfatal** — `gh` fails, verify warning logged but no exception

13. **test_kill_then_next_run_no_spurious_transition** — simulate two consecutive runs: Run 1 detects kill condition, executes kill, persists `"killed"` state. Run 2 sees marker, returns `"killed"`, state is `killed → killed` — verify no transition alert is generated.

### Integration test (manual)

1. Append synthetic error lines to a test log file
2. Run `python3 system_monitor.py --dry-run`
3. Verify kill detection triggers with correct detail
4. Verify no actual stop/unload/pkill happens in dry-run mode

## Edge Cases

- **Log file missing or empty:** Return healthy. No log = no evidence of token burn.
- **Log file very large:** Reverse-read strategy handles this; never reads more than needed.
- **Timestamps can't be parsed:** Skip unparseable lines; don't count them.
- **Both patterns trigger simultaneously:** Kill on whichever is detected first. Report both counts in the marker.
- **OpenClaw already dead/unloaded:** Kill marker already exists → `check_openclaw_token_health()` returns `"killed"` (stable state, no action). The existing `check_openclaw()` also returns `"killed"`.
- **Alfred and Telegram both down:** Log the alert to `system-monitor.log`. The kill still executes — protecting the budget is the priority even if notification fails.
- **Kill marker exists but OpenClaw is somehow running:** This shouldn't happen (plist unloaded), but if it does, `check_openclaw_token_health()` still scans logs and can kill again.
