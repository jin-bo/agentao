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


_PATH_RE = re.compile(r'[\w./\\-]+\.\w{2,6}')


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


def load_project_instructions(
    working_directory: Path,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    """Load project-specific instructions from ``AGENTAO.md`` if present.

    Returns the file contents or ``None`` when the file is absent or
    cannot be read. Errors are logged at WARNING and swallowed — the
    agent should still start when the project has no AGENTAO.md.
    """
    try:
        agentao_md = working_directory / "AGENTAO.md"
        if agentao_md.exists():
            content = agentao_md.read_text(encoding="utf-8")
            if logger is not None:
                logger.info(f"Loaded project instructions from {agentao_md}")
            return content
    except Exception as exc:
        if logger is not None:
            logger.warning(f"Could not load AGENTAO.md: {exc}")
    return None
