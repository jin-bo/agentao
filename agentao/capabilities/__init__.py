"""Capability protocols for embedded harness IO routing.

Embedded hosts inject these protocols to redirect filesystem, shell,
and persistent-memory IO through their own infrastructure (Docker
exec, virtual FS, audit proxies, remote runners, Redis, Postgres, …).
When omitted, ``LocalFileSystem`` / ``LocalShellExecutor`` /
``SQLiteMemoryStore`` provide byte-equivalent default behavior.
"""

from .filesystem import (
    FileEntry,
    FileStat,
    FileSystem,
    LocalFileSystem,
)
from .memory import MemoryStore
from .mcp import MCPRegistry
from .shell import (
    BackgroundHandle,
    LocalShellExecutor,
    ShellExecutor,
    ShellRequest,
    ShellResult,
)
from ..memory.storage import SQLiteMemoryStore
from ..mcp.registry import FileBackedMCPRegistry, InMemoryMCPRegistry

__all__ = [
    "FileEntry",
    "FileStat",
    "FileSystem",
    "LocalFileSystem",
    "BackgroundHandle",
    "ShellExecutor",
    "ShellRequest",
    "ShellResult",
    "LocalShellExecutor",
    "MemoryStore",
    "SQLiteMemoryStore",
    "MCPRegistry",
    "FileBackedMCPRegistry",
    "InMemoryMCPRegistry",
]
