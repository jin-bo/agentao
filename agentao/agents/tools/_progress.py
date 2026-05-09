"""``SubagentProgress`` — structured sub-agent lifecycle event.

Replaces plain-text sentinel strings (``_AGENT_START`` / ``_AGENT_END``)
that the step callback used to receive. The CLI's display layer pattern-
matches on the sentinel + reads structured fields off this dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..bg_store import BgTaskStatus


@dataclass
class SubagentProgress:
    """Structured sub-agent lifecycle event, passed via step_callback.

    Replaces plain-text sentinel strings (_AGENT_START / _AGENT_END).
    The step_callback receives (sentinel_name, SubagentProgress) where
    sentinel_name is AgentToolWrapper._AGENT_START or _AGENT_END.
    """
    agent_name: str
    state: BgTaskStatus
    task: str = ""
    max_turns: int = 0
    turns: int = 0
    tool_calls: int = 0
    tokens: int = 0
    duration_ms: int = 0
    result: Optional[str] = None
    error: Optional[str] = None
