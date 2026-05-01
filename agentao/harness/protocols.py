"""Capability protocols re-exported on the public harness surface.

Embedded hosts override IO by injecting these ``Protocol`` types into
``Agentao(filesystem=..., shell=..., mcp_registry=..., memory_store=...)``.
Importing them directly from ``agentao.harness.protocols`` keeps host code
on the stable boundary instead of reaching into ``agentao.capabilities.*``,
which is internal and may move.

The value types (``FileEntry``, ``FileStat``, ``ShellRequest``,
``ShellResult``, ``BackgroundHandle``) are part of the public contract too:
hosts implementing a ``Protocol`` must produce these shapes.

See ``docs/api/harness.md`` for the host-injection walkthrough.
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
