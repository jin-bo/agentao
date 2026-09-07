"""Capability protocols re-exported on the public ``agentao.host`` surface.

This is the only inbound surface in ``agentao.host``: the other three
pillars (events, ACP schema, permission state) flow Agentao → host;
these protocols flow host → Agentao, letting embedded hosts override
IO by injecting their own implementations into
``Agentao(filesystem=..., shell=..., mcp_registry=..., memory_manager=...)``.

Importing them directly from ``agentao.host.protocols`` keeps host code
on the stable boundary instead of reaching into ``agentao.capabilities.*``,
which is internal and may move.

The value types (``FileEntry``, ``FileStat``, ``ShellRequest``,
``ShellResult``, ``BackgroundHandle``) are part of the public contract too:
hosts implementing a ``Protocol`` must produce these shapes.

``ShellRequest`` carries a discriminated ``LaunchRequest`` rather than a
command string, so the launch shapes (``LegacyLaunch`` for the platform's own
shell, ``WindowsLaunch`` for a named interpreter) and the two answers a
``ShellSpecProvider`` can give (``ShellSpec``, ``Exhausted``) are part of the
same contract — a host cannot build a request or declare its interpreter
without them. They are re-exported here for the same reason as everything else
in this module: so host code never has to reach into ``agentao.capabilities.*``.

See ``docs/reference/host-api.md`` for the host-injection walkthrough.
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
from ..capabilities.shell_spec import (
    AbsPath,
    Exhausted,
    LaunchRequest,
    LegacyLaunch,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    ShellSpecProvider,
    WindowsLaunch,
)

__all__ = [
    "AbsPath",
    "BackgroundHandle",
    "Exhausted",
    "FileEntry",
    "FileStat",
    "FileSystem",
    "LaunchRequest",
    "LegacyLaunch",
    "MCPRegistry",
    "MemoryStore",
    "ShellBlock",
    "ShellDialect",
    "ShellExecutor",
    "ShellRequest",
    "ShellResult",
    "ShellSpec",
    "ShellSpecProvider",
    "WindowsLaunch",
]
