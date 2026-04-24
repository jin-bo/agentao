"""Tool registration boundary extracted from ``agentao.agent``.

This subpackage holds the one-shot registration routines that populate
``Agentao.tools`` during construction. They stay as module-level
functions (not classes): they mutate the passed-in agent once at init
and have no lifecycle of their own.

Split by concern so each file is small and independently testable:

- ``registry``     — built-in tools (file, shell, web, memory, skills…)
- ``agent_tools``  — sub-agent tools built on top of ``AgentManager``
- ``mcp_tools``    — MCP config load + remote tool discovery
"""

from .agent_tools import register_agent_tools
from .mcp_tools import init_mcp
from .registry import register_builtin_tools

__all__ = [
    "register_builtin_tools",
    "register_agent_tools",
    "init_mcp",
]
