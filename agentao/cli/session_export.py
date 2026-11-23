"""Render a conversation as a Markdown transcript (#664).

``/export`` writes the result. Reads history only, so it works with replay
off. No Rich or console imports.

What a transcript holds:

- **User turns**: only a user's own requests (``is_user_request``). History
  also has ``user`` messages the runtime wrote: background agent updates,
  Stop hook follow-ups, ``Skill loaded`` notes. Those can hold text the user
  never wrote, so they are left out. The date reminder and hook context
  the runtime puts in front are removed; a non-text part becomes a
  placeholder such as ``[image]``. A ``/goal`` continuation prompt is sent
  as a turn, so it shows as one.
- **Assistant replies**: the reply text. Reasoning (``reasoning_content``
  and the other thinking carriers) is never exported.
- **Tool calls and results**: only with ``include_tools``. Each result is
  capped at ``MAX_TOOL_RESULT_CHARS``.
- **System messages**: never. When history holds what a compaction wrote,
  the transcript says so at the top and where the summary was, because the
  messages before it are gone.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from ..compaction.request import (
    _TURN_REMINDER_RE,
    is_kept_request,
    is_user_request,
    strip_kept_note,
)
from ..context_manager import _COMPACTION_SCAFFOLDING_PREFIXES

MAX_TOOL_RESULT_CHARS = 4000

_COMPACTED_NOTE = (
    "> **Note:** earlier messages were compacted into a summary, so this "
    "transcript covers only the history that was kept."
)
_COMPACTED_MARK = "*— Earlier messages were compacted here (summary not included). —*"


def _is_compaction_message(msg: Dict[str, Any]) -> bool:
    content = msg.get("content")
    return (
        msg.get("role") == "system"
        and isinstance(content, str)
        and content.startswith(_COMPACTION_SCAFFOLDING_PREFIXES)
    )


def _part_text(part: Any) -> str:
    if not isinstance(part, dict):
        return ""
    kind = part.get("type")
    if kind == "text":
        text = part.get("text")
        return text if isinstance(text, str) else ""
    if kind in ("image_url", "image", "input_image"):
        return "[image]"
    return f"[{kind} attachment]" if isinstance(kind, str) and kind else "[attachment]"


def _text(content: Any) -> str:
    """A message's text, with a placeholder for each non-text part."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(t for t in (_part_text(p) for p in content) if t)
    return ""


def _fence(text: str, lang: str = "") -> str:
    """``text`` in a code fence longer than any backtick run inside it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _cap(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    cut = len(text) - MAX_TOOL_RESULT_CHARS
    return f"{text[:MAX_TOOL_RESULT_CHARS]}\n… ({cut} more characters not exported)"


def _tool_calls(msg: Dict[str, Any], call_names: Dict[str, str]) -> Iterable[str]:
    for call in msg.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = fn.get("name") if isinstance(fn.get("name"), str) else "?"
        if isinstance(call.get("id"), str):
            call_names[call["id"]] = name
        args = fn.get("arguments")
        block = f"**Tool call:** `{name}`"
        if isinstance(args, str) and args.strip():
            block += "\n\n" + _fence(_cap(args), "json")
        yield block


# Context a ``UserPromptSubmit`` hook added in front of the user's text
# (``runtime/chat_loop/_hook_dispatch.py``). The hook wrote it, not the user.
_HOOK_CONTEXT_RE = re.compile(
    r"(?:<user-prompt-submit-hook>\n.*?\n</user-prompt-submit-hook>\n)+", re.DOTALL,
)


def _request_text(msg: Dict[str, Any]) -> str:
    """What the user typed in a request: the runtime's prefixes removed.

    Only what the runtime puts *in front* is removed (the kept-request note,
    the date reminder, hook context), each by its exact form. A
    ``<system-reminder>`` the user typed further in stays.
    """
    text = strip_kept_note(_text(msg.get("content")))
    m = _TURN_REMINDER_RE.match(text)
    if m:
        text = text[m.end():]
    m = _HOOK_CONTEXT_RE.match(text)
    if m:
        text = text[m.end():]
    return text.strip()


def first_request_text(messages: List[Dict[str, Any]]) -> str:
    """The first user request's text, for a default title."""
    for msg in messages:
        if is_user_request(msg):
            text = _request_text(msg)
            if text:
                return " ".join(text.split())[:60]
    return ""


def render_markdown(
    messages: List[Dict[str, Any]],
    *,
    title: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    include_tools: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """The Markdown transcript of ``messages``. See the module docstring."""
    body: List[str] = []
    compacted = False
    call_names: Dict[str, str] = {}  # tool_call id -> tool name
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if _is_compaction_message(msg):
            if not compacted:
                body.append(_COMPACTED_MARK)
            compacted = True
        elif role == "user" and is_user_request(msg):
            if is_kept_request(msg) and not compacted:
                # The last overflow rung writes no summary: the kept request
                # is then the only trace that earlier messages are gone.
                body.append(_COMPACTED_MARK)
                compacted = True
            text = _request_text(msg)
            if text:
                body.append(f"## User\n\n{text}")
        elif role == "assistant":
            parts = []
            text = _text(msg.get("content")).strip()
            if text:
                parts.append(text)
            if include_tools:
                parts.extend(_tool_calls(msg, call_names))
            if parts:
                body.append("## Assistant\n\n" + "\n\n".join(parts))
        elif role == "tool" and include_tools:
            name = msg.get("name") if isinstance(msg.get("name"), str) else None
            name = name or call_names.get(msg.get("tool_call_id") or "", "tool")
            text = _text(msg.get("content"))
            body.append(f"**Tool result:** `{name}`\n\n" + _fence(_cap(text)))

    stamp = (now or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M:%S %z").strip()
    head = [f"# {' '.join(title.split()) or 'Agentao conversation'}", ""]
    if session_id:
        head.append(f"- Session: `{session_id}`")
    if model:
        head.append(f"- Model: `{model}`")
    head.append(f"- Exported: {stamp}")
    head.append(
        "- Contents: user and assistant messages"
        + (", tool calls and results" if include_tools else "")
    )
    if compacted:
        head += ["", _COMPACTED_NOTE]
    if not body:
        body.append("*No user or assistant messages to export.*")
    return "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n"
