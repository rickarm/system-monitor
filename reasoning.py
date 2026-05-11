"""Claude Agent SDK integration for system-monitor.

Invoked only on state transitions to reason about root cause and compose alerts.
"""

import asyncio
import logging
from telegram import format_degraded_context

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Mac mini infrastructure monitor. When services change status, you:
1. Analyze the evidence to determine the likely root cause
2. Assess severity (critical / warning / info)
3. Compose a concise Telegram alert in HTML format

Output ONLY the Telegram message — no preamble, no markdown fences.

Format:
- Use <b>bold</b> for service names and status
- Use <code>monospace</code> for fix commands
- Use <i>italic</i> for context
- Keep it under 500 characters
- Lead with an emoji: 🔴 for degraded, 🟢 for recovered
- Include the fix command if one is suggested
"""


def build_prompt(transitions: list[dict]) -> str:
    """Build the prompt for Claude with transition context."""
    context = format_degraded_context(transitions)
    return f"""\
The following service status transitions were detected on the Mac mini:

{context}

Compose a Telegram alert message. Reason briefly about the likely cause, \
then output the HTML-formatted message to send. If multiple services changed, \
note whether they might be related (e.g., a shared dependency like launchd or network)."""


async def reason_about_transitions(transitions: list[dict]) -> str | None:
    """Invoke Claude via Agent SDK to reason about transitions and compose an alert.

    Returns the composed Telegram message, or None if reasoning fails.
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

        prompt = build_prompt(transitions)
        result_text = None

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=SYSTEM_PROMPT,
                allowed_tools=["Read", "Bash"],
                max_turns=3,
                model="claude-haiku-4-5",
            ),
        ):
            if isinstance(message, ResultMessage):
                result_text = message.result

        return result_text
    except Exception as e:
        log.error("Claude reasoning failed: %s", e)
        return None


def reason_sync(transitions: list[dict]) -> str | None:
    """Synchronous wrapper for reason_about_transitions."""
    return asyncio.run(reason_about_transitions(transitions))
