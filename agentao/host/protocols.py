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

``ShellRequest`` now carries a discriminated ``LaunchRequest`` rather than a
command string, so the launch shapes (``LegacyLaunch`` for the policy-off
rungs, ``PosixLaunch`` / ``WindowsLaunch`` for the attested ones) and the two
answers a ``ShellSpecProvider`` can give (``ShellSpec``, ``Exhausted``) are
part of the same contract — a host cannot build a request or declare its
interpreter without them. They are re-exported here for the same reason as
everything else in this module: so host code never has to reach into
``agentao.capabilities.*``.

A host whose executor runs commands somewhere other than this machine also
supplies its own ``IdentityOracle``: access masks, reparse points, signatures
and the target's own environment are facts about the machine the command will
run on, and a floor answering them from *this* machine would be attesting the
wrong filesystem. ``ReparseResult`` / ``ReparseState``, ``SessionConfig``,
``PinnedEnv``, ``ResolvedImage`` and ``LauncherIdentity`` are the shapes that
oracle returns.

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
    LauncherIdentity,
    LaunchRequest,
    LegacyLaunch,
    PinnedEnv,
    PosixLaunch,
    ResolvedImage,
    Sha256,
    ShellSpec,
    ShellSpecProvider,
    Subject,
    WindowsLaunch,
)
from ..permissions_hardline._trust import (
    IdentityOracle,
    ReparseResult,
    ReparseState,
    SessionConfig,
)

__all__ = [
    "AbsPath",
    "BackgroundHandle",
    "Exhausted",
    "FileEntry",
    "FileStat",
    "FileSystem",
    "IdentityOracle",
    "LaunchRequest",
    "LauncherIdentity",
    "LegacyLaunch",
    "MCPRegistry",
    "MemoryStore",
    "PinnedEnv",
    "PosixLaunch",
    "ReparseResult",
    "ReparseState",
    "ResolvedImage",
    "SessionConfig",
    "Sha256",
    "ShellExecutor",
    "ShellRequest",
    "ShellResult",
    "ShellSpec",
    "ShellSpecProvider",
    "Subject",
    "WindowsLaunch",
]
