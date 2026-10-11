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

    On ``_AGENT_END`` a counter is ``None`` when it is not known, not zero:
    a foreground run that raised before its sub-agent ran has no history to
    count and no run to time.
    """
    agent_name: str
    state: BgTaskStatus
    task: str = ""
    max_turns: int = 0
    turns: Optional[int] = 0
    tool_calls: Optional[int] = 0
    tokens: Optional[int] = 0
    duration_ms: Optional[int] = 0
    result: Optional[str] = None
    error: Optional[str] = None
