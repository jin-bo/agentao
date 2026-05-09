"""SubAgent tool wrappers — core components for the agent-as-tool pattern.

Background-task state (registry, cancellation tokens, notification queue,
persistence) lives on a per-Agentao :class:`BackgroundTaskStore`. The
three tools here take a store reference at construction time and read
or write through it.

Layout (each row only depends on rows above):
    _progress   ← SubagentProgress (lifecycle event dataclass)
    _complete   ← TaskComplete + CompleteTaskTool (terminal signal)
    _bg_tools   ← CheckBackgroundAgentTool + CancelBackgroundAgentTool
    _wrapper    ← AgentToolWrapper (the agent-as-tool driver)
"""

from __future__ import annotations

from ._bg_tools import CancelBackgroundAgentTool, CheckBackgroundAgentTool
from ._complete import CompleteTaskTool, TaskComplete
from ._progress import SubagentProgress
from ._wrapper import AgentToolWrapper

__all__ = [
    "AgentToolWrapper",
    "CancelBackgroundAgentTool",
    "CheckBackgroundAgentTool",
    "CompleteTaskTool",
    "SubagentProgress",
    "TaskComplete",
]
