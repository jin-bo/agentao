"""Runtime primitives extracted from ``agentao.agent``.

Groups the stateful, per-turn machinery so new agent features have a
home that is not ``Agentao`` itself:

- ``chat_loop.ChatLoopRunner``  — single-turn LLM + tool-call loop body
- ``tool_runner.ToolRunner``    — 4-phase tool execution pipeline

These are imported by :class:`agentao.agent.Agentao` during construction.
The public ``Agentao.chat()`` / ``tool_runner`` attribute contract is
preserved for external users of the library.
"""

from .chat_loop import ChatLoopRunner
from .model import list_available_models, set_model, set_provider
from .tool_runner import ToolRunner

__all__ = [
    "ChatLoopRunner",
    "ToolRunner",
    "list_available_models",
    "set_model",
    "set_provider",
]
