"""Capability protocols re-exported on the public ``agentao.host`` surface.

This is the only inbound surface in ``agentao.host``: the other three
pillars (events, ACP schema, permission state) flow Agentao → host;
these protocols flow host → Agentao, letting embedded hosts override
IO by injecting their own implementations into
``Agentao(filesystem=..., shell=..., mcp_registry=..., memory_store=...)``.

Importing them directly from ``agentao.host.protocols`` keeps host code
on the stable boundary instead of reaching into ``agentao.capabilities.*``,
which is internal and may move.

The value types (``FileEntry``, ``FileStat``, ``ShellRequest``,
``ShellResult``, ``BackgroundHandle``) are part of the public contract too:
hosts implementing a ``Protocol`` must produce these shapes.

See ``docs/api/host.md`` for the host-injection walkthrough.
"""

from __future__ import annotations

from ..capabilities.filesystem import FileEntry, FileStat, FileSystem
from ..capabilities.mcp import MCPRegistry
from ..capabilities.memory import MemoryStore
from ..capabilities.shell import (
    BackgroundHandle,
    ShellExecutor,
    ShellRequest,
    ShellResult,
)

__all__ = [
    "BackgroundHandle",
    "FileEntry",
    "FileStat",
    "FileSystem",
    "MCPRegistry",
    "MemoryStore",
    "ShellExecutor",
    "ShellRequest",
    "ShellResult",
]
