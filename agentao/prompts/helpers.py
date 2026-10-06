"""Prompt-context helpers extracted from ``agentao/agent.py``.

These two helpers are consumed by :class:`agentao.prompts.SystemPromptBuilder`
and by the agent's construction path (AGENTAO.md). Keeping them here
means the agent core no longer owns the text-extraction / file-reading
logic — it just wires results into state. The agent's public method
surface is preserved via thin facades on ``Agentao``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..frontmatter import match_frontmatter

_PATH_RE = re.compile(r'[\w./\\-]+\.\w{2,6}')


def strip_frontmatter(content: str) -> str:
    """Drop a leading YAML frontmatter block, returning just the body.

    Stripping happens only when the document genuinely opens with a
    frontmatter block — an opening ``---`` fence, a YAML *mapping*, and a
    closing ``---`` fence. If the block is absent, malformed YAML, or parses
    to a non-mapping (e.g. a stray ``---`` horizontal rule wrapping prose),
    the content is returned untouched so real instructions are never silently
    dropped.
    """
    match = match_frontmatter(content)
    if match is None:
        return content
    try:
        meta = yaml.safe_load(match.group("meta"))
    except yaml.YAMLError:
        return content
    if not isinstance(meta, dict):
        return content
    return match.group("body").lstrip("\n")


def extract_context_hints(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract file paths from the last ~10 messages as recall hints.

    Handles both shapes the chat path can produce:

    - Plain string ``content``.
    - List of typed blocks (multimodal/tool-use); the canonical text
      block is ``{"type": "text", "text": "..."}``, matching how
      :meth:`ContextManager._format_for_summary` and
      :meth:`MemoryCrystallizer._user_message_text` consume them.
    """
    hints: List[str] = []
    for msg in messages[-10:]:
        content = msg.get("content", "")
        if isinstance(content, str):
            hints.extend(_PATH_RE.findall(content))
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    hints.extend(_PATH_RE.findall(str(block.get("text", ""))))
    return hints[:20]


# Read in this order; the first file that exists is the only one used.
# ``AGENTS.md`` is the cross-tool convention (agents.md); it is read only when
# a project has no ``AGENTAO.md``, so a project that has both keeps the
# behaviour it had. Root of ``working_directory`` only — no nested lookup.
PROJECT_INSTRUCTION_FILES = ("AGENTAO.md", "AGENTS.md")


def load_project_instructions(
    working_directory: Path,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    """Load project-specific instructions from ``AGENTAO.md``, else ``AGENTS.md``.

    Only the first of :data:`PROJECT_INSTRUCTION_FILES` that exists is read;
    the two are never merged. A leading YAML frontmatter block (e.g. carried
    over from a Cursor rule or another tool's instruction file) is stripped
    via :func:`strip_frontmatter` so it does not leak into the system prompt.
    Returns the (frontmatter-free) file contents or ``None`` when neither
    file is present or the chosen one cannot be read. Errors are logged at
    WARNING and swallowed — the agent should still start. A file that exists
    but cannot be read does not fall through to the next name: the project
    meant that file, and reading a different one would be a silent swap.
    """
    for name in PROJECT_INSTRUCTION_FILES:
        path = working_directory / name
        try:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            if logger is not None:
                logger.warning(f"Could not load {name}: {exc}")
            return None
        body = strip_frontmatter(content)
        if logger is not None:
            if body != content:
                logger.info(
                    f"Loaded project instructions from {path} "
                    f"(ignored leading YAML frontmatter)"
                )
            else:
                logger.info(f"Loaded project instructions from {path}")
        return body
    return None
