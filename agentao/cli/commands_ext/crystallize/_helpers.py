"""Text-shaping helpers for ``/crystallize`` evidence + suggestions.

Pure helpers that the evidence collector, suggest renderer, and the
feedback flow all share. They have no dependencies on rich console or
``cli`` state — extracted so the higher-level modules read top-down
without being interrupted by single-purpose string utilities.
"""

from __future__ import annotations

import json as _json
import re as _re
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ...app import AgentaoCLI


# Argument keys that most often carry a concrete file path.
_PATHY_ARG_KEYS = (
    "file_path", "path", "filename", "file", "target", "source", "dest",
    "input_file", "output_file",
)

_SENTENCE_SPLIT_RE = _re.compile(r"(?<=[.。!?！？\n])\s+")
_FILE_HINT_RE = _re.compile(
    r"(?:(?<=[\s`(])|^)((?:/|\./|[A-Za-z0-9_\-.]+/)[A-Za-z0-9_\-./]+\.[A-Za-z0-9]+)"
)


def _sanitize_skill_name(raw: str) -> str:
    return _re.sub(r'[^a-z0-9-]', '-', (raw or "").lower()).strip('-')


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_sentences(text: str, *, max_chars: int = 200) -> str:
    """Return the first sentence(s) of text, capped at ``max_chars``."""
    text = (text or "").strip()
    if not text:
        return ""
    first = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    return _clip(first, max_chars)


def _message_text(content: Any) -> str:
    """Normalize a message content field to a single string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and b.get("text"):
                    parts.append(str(b["text"]))
                elif "content" in b and isinstance(b["content"], str):
                    parts.append(b["content"])
        return "\n".join(p for p in parts if p)
    return ""


def _parse_tool_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = _json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(data, dict):
            return data
    return {}


def _short_args_summary(args: Dict[str, Any]) -> str:
    """Build a one-line summary from tool args, clipping long values."""
    if not args:
        return ""
    parts: list[str] = []
    for key, val in list(args.items())[:4]:
        val_str = str(val).replace("\n", " ")
        val_str = _clip(val_str, 80)
        parts.append(f"{key}={val_str}")
    return ", ".join(parts)


def _detect_key_paths(blob: str) -> List[str]:
    if not blob:
        return []
    return list(dict.fromkeys(_FILE_HINT_RE.findall(blob)))


def _collect_session_content(cli: AgentaoCLI) -> str:
    """Merge compacted session summaries + live conversation turns."""
    session_content = ""
    summaries = cli.agent.memory_manager.get_recent_session_summaries(limit=5)
    if summaries:
        session_content = "\n\n---\n\n".join(s.summary_text for s in reversed(summaries))

    live_parts = []
    for msg in cli.agent.messages:
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            live_parts.append(f"{role.capitalize()}: {content}")
    if live_parts:
        live_section = "\n".join(live_parts)
        session_content = (session_content + "\n\n" + live_section).strip()
    return session_content
