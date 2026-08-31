"""Rendering helpers for ACP inbox messages.

Produces concise, borderless console output for ACP server messages.
Tool-related messages (tool_call, tool_call_update) are logged only —
they are internal execution details that clutter the user's view.
Agent text output (agent_message_chunk) is rendered as Markdown.

The renderer is intentionally decoupled from the inbox so it can be tested
and used independently (e.g. by a future TUI or web frontend).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from ..security.terminal_text import sanitize_terminal_text
from .inbox import InboxMessage, MessageKind

logger = logging.getLogger("agentao.acp_client")

# Try Rich import; fall back to plain text if unavailable.
try:
    from rich.console import Console

    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


# ---------------------------------------------------------------------------
# Update kinds that are log-only (not displayed to user)
# ---------------------------------------------------------------------------

_LOG_ONLY_KINDS = {
    "tool_call",
    "tool_call_update",
    "agent_thought_chunk",
    "user_message_chunk",
}

# Update kinds whose text is the LLM reply (displayed as Markdown)
_MARKDOWN_KINDS = {
    "agent_message_chunk",
}

# Upper bound on the *aggregate* agent text rendered in one flush. Each chunk is
# already capped (helpers._cap_chunk, 256 KiB), but flush_to_console accumulates
# every same-server agent_message_chunk in a batch before one RichMarkdown parse;
# a burst of near-cap chunks could concatenate into tens of MB and stall the UI.
# This bounds the string handed to RichMarkdown — far above any real reply.
_MAX_AGENT_RENDER_CHARS = 1024 * 1024


# ---------------------------------------------------------------------------
# Style mapping
# ---------------------------------------------------------------------------

_KIND_PREFIXES = {
    MessageKind.RESPONSE: ("cyan", ""),
    MessageKind.NOTIFICATION: ("dim", ""),
    MessageKind.PERMISSION: ("yellow", "permission"),
    MessageKind.INPUT: ("magenta", "input"),
    MessageKind.ERROR: ("red", "error"),
}


# ---------------------------------------------------------------------------
# Terminal-escape sanitization (server-controlled display text)
# ---------------------------------------------------------------------------

# The implementation moved to ``agentao/security/terminal_text.py`` when the
# reference CLI turned out to need the same defense on its own display
# boundaries (tool-confirmation arguments, ``ask_user`` prompts). AC5's
# rationale lives there. The private alias is kept because it is this module's
# established name for the transform and tests import it by that name.
_sanitize_terminal_text = sanitize_terminal_text


# ---------------------------------------------------------------------------
# Plain-text fallback
# ---------------------------------------------------------------------------


def render_plain(msg: InboxMessage) -> str:
    """Render a single message as a concise plain-text line."""
    ts = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")
    kind_label = (
        f" [{msg.kind.value}]"
        if msg.kind not in (MessageKind.RESPONSE, MessageKind.NOTIFICATION)
        else ""
    )
    text = _sanitize_terminal_text(msg.text).replace("\n", "\n  ")
    return f"[{msg.server}]{kind_label} {text}  ({ts})"


def render_all_plain(messages: List[InboxMessage]) -> str:
    """Render a list of messages as plain text."""
    if not messages:
        return ""
    return "\n".join(render_plain(m) for m in messages)


# ---------------------------------------------------------------------------
# Rich rendering (no borders)
# ---------------------------------------------------------------------------


def flush_to_console(
    messages: List[InboxMessage],
    console: Optional["Console"] = None,
    *,
    markdown_mode: bool = True,
) -> int:
    """Render and print inbox messages to a Rich console.

    - Tool messages (``tool_call``, ``tool_call_update``) are logged at
      debug level and NOT shown to the user.
    - Agent text (``agent_message_chunk``) is rendered as Markdown when
      ``markdown_mode`` is True.
    - Permission/input/error messages are shown with colored prefixes.

    Args:
        messages: Messages to render (typically from :meth:`Inbox.drain`).
        console: Rich console instance.  If ``None``, a default is created.
        markdown_mode: Whether to render agent text as Markdown.

    Returns:
        Number of messages rendered (excludes log-only messages).
    """
    if not messages:
        return 0

    if not _HAS_RICH or console is None:
        import sys
        sys.stdout.write(render_all_plain(messages) + "\n")
        sys.stdout.flush()
        return len(messages)

    from rich.markdown import Markdown as RichMarkdown
    from rich.markup import escape

    rendered = 0
    # Accumulate agent_message_chunk text for Markdown rendering.
    agent_text_parts: List[str] = []
    current_server: str = ""

    def _flush_agent_text() -> int:
        """Render accumulated agent text as Markdown (or plain)."""
        nonlocal agent_text_parts, current_server
        if not agent_text_parts:
            return 0
        full_text = "".join(agent_text_parts)
        agent_text_parts = []
        if not full_text.strip():
            return 0
        if len(full_text) > _MAX_AGENT_RENDER_CHARS:
            dropped = len(full_text) - _MAX_AGENT_RENDER_CHARS
            full_text = (
                full_text[:_MAX_AGENT_RENDER_CHARS]
                + f"...[truncated {dropped} chars]"
            )
        console.print()
        if markdown_mode:
            console.print(RichMarkdown(full_text))
        else:
            console.print(full_text)
        console.print()
        return 1

    for msg in messages:
        # Log-only messages: record in debug log, skip display.
        if msg.update_kind in _LOG_ONLY_KINDS:
            logger.debug(
                "acp[%s] %s: %s",
                msg.server,
                msg.update_kind,
                # Sanitize before logging: a DEBUG stream handler can echo this
                # to the terminal, so server escapes must not bypass AC5 here.
                _sanitize_terminal_text(msg.text[:200]) if msg.text else "(empty)",
            )
            continue

        # Permission/input messages are displayed by the inline interaction
        # handler (_handle_inline_interaction), so skip them here to avoid
        # duplicate display.
        if msg.kind in (MessageKind.PERMISSION, MessageKind.INPUT):
            continue

        # Agent message chunks: accumulate for Markdown rendering.
        if msg.update_kind in _MARKDOWN_KINDS:
            if current_server and current_server != msg.server:
                rendered += _flush_agent_text()
            current_server = msg.server
            if msg.text:
                agent_text_parts.append(_sanitize_terminal_text(msg.text))
            continue

        # Flush any accumulated agent text before showing other messages.
        rendered += _flush_agent_text()

        # Skip empty messages.
        if not msg.text and msg.kind in (
            MessageKind.RESPONSE,
            MessageKind.NOTIFICATION,
        ):
            continue

        color, label = _KIND_PREFIXES.get(msg.kind, ("dim", ""))
        ts = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")

        safe_name = escape(msg.server)
        prefix = f"[bold {color}]\\[{safe_name}][/bold {color}]"
        if label:
            prefix += f" [{color}]{label}:[/{color}]"

        text = _sanitize_terminal_text(msg.text or "")
        lines = text.split("\n")
        first = lines[0]
        rest = lines[1:]

        console.print(f"{prefix} {escape(first)}  [dim]({ts})[/dim]")
        for line in rest:
            console.print(f"  {escape(line)}")
        rendered += 1

    # Flush trailing agent text.
    rendered += _flush_agent_text()

    return rendered
