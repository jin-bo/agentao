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

# Re-imported so ``test_async_tool.py`` can resolve ``AgentToolWrapper.__init__``'s
# string-form annotations against ``vars(agentao.agents.tools)``: the test passes
# the package globalns to :func:`typing.get_type_hints`, which fails when the
# referenced names live only in ``_wrapper``'s namespace post-split.
# noqa: F401 — names are intentionally bound on the package surface.
from typing import Any, Callable, Dict, List, Optional, Tuple  # noqa: F401

from ...tools.base import RegistrableTool  # noqa: F401
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
