"""``SubagentProgress`` — the sub-agent lifecycle payload of the legacy
``step_callback``.

Only ``build_compat_transport`` builds one now: it maps ``AGENT_START`` /
``AGENT_END`` events back to ``step_callback("__agent_start__" |
"__agent_end__", SubagentProgress(...))`` for hosts still on the callback
API. Agentao's own sub-agents send those events directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..bg_store import BgTaskStatus


@dataclass
class SubagentProgress:
    """Structured sub-agent lifecycle event, passed via step_callback.

    The step_callback receives ``(sentinel_name, SubagentProgress)``, where
    ``sentinel_name`` is ``"__agent_start__"`` or ``"__agent_end__"``. A
    counter the ``AGENT_END`` event left out (not known) reads as 0 here.
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
