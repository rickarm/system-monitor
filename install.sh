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

launchctl load -w "$PLIST_DEST"

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
