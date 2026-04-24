"""Compatibility shim for the old ``agentao.tool_runner`` module path.

The implementation moved to :mod:`agentao.runtime.tool_runner`. Existing
consumers (docs, integrations, user code) that still import from
``agentao.tool_runner`` continue to work by re-exporting the public
symbols from the new location.
"""

from __future__ import annotations

from .runtime.tool_runner import *  # noqa: F401,F403
from .runtime import tool_runner as _runtime_tool_runner

__all__ = getattr(_runtime_tool_runner, "__all__", None) or [
    name for name in dir(_runtime_tool_runner) if not name.startswith("_")
]


def __getattr__(name: str):
    return getattr(_runtime_tool_runner, name)


def __dir__():
    return sorted(set(list(globals().keys()) + dir(_runtime_tool_runner)))
