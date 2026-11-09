"""A user's own turn in history, and the copy of it that compaction keeps.

A cut can land in the middle of a turn. The request that started the turn then
falls into the summarized half, and a summary is not a safe home for it: the
summarizer sees a clipped copy, and nothing makes it restate the request. So
compaction keeps the request itself, right after the summary
(``ContextManager._request_to_keep``).

The chat loop opens every user turn with the date reminder, and so does a
host's ``new_instruction`` at the iteration cap. Nothing else writes one. That
reminder is what tells a request from the other ``user``
messages in history: a background agent update, a Stop hook follow-up, a
``Skill loaded`` note. Any of those can hold text the user never wrote, so this
test is on the exact reminder, never on "any leading ``<system-reminder>``".

Standard library only, like ``types.py``: ``context_manager.py`` imports this
at module level, and it must not load the coordinator.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

#: Opens the copy of a request that compaction keeps. A user's own turn starts
#: with the date reminder, so a user cannot write this prefix into a turn.
KEPT_REQUEST_NOTE = (
    "<system-reminder>\n"
    "Compaction removed earlier messages and kept this request of the turn in "
    "progress. A summary above, if there is one, records the work done so far. "
    "Continue that work. Do not redo finished work.\n"
    "</system-reminder>\n"
)

_TURN_REMINDER_RE = re.compile(
    r"<system-reminder>\nCurrent Date/Time: [^\n]*\n</system-reminder>\n"
)


def turn_reminder(now: datetime) -> str:
    """The date reminder that opens a user turn. ``_TURN_REMINDER_RE`` reads it."""
    return (
        f"<system-reminder>\n"
        f"Current Date/Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})\n"
        f"</system-reminder>\n"
    )


def _leading_text(content: Any) -> Optional[str]:
    """The text a message opens with: its string, or a multimodal first text part."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text")
            return text if isinstance(text, str) else None
    return None


def is_kept_request(message: Any) -> bool:
    """Whether ``message`` is the copy of a request that an earlier compaction kept."""
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content")
    return (
        isinstance(content, str)
        and content.startswith(KEPT_REQUEST_NOTE)
        and _TURN_REMINDER_RE.match(content, len(KEPT_REQUEST_NOTE)) is not None
    )


def is_user_request(message: Any) -> bool:
    """Whether ``message`` is a user's own turn, or the kept copy of one."""
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    if is_kept_request(message):
        return True
    text = _leading_text(message.get("content"))
    return text is not None and _TURN_REMINDER_RE.match(text) is not None


def strip_kept_note(text: str) -> str:
    """``text`` without the kept-request note. Other text comes back unchanged."""
    if text.startswith(KEPT_REQUEST_NOTE):
        return text[len(KEPT_REQUEST_NOTE):]
    return text
