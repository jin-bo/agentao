"""Capability protocols for embedded harness IO routing.

Embedded hosts inject these protocols to redirect filesystem and shell
IO through their own infrastructure (Docker exec, virtual FS, audit
proxies, remote runners, …). When omitted, ``LocalFileSystem`` and
``LocalShellExecutor`` provide byte-equivalent default behavior.
"""

from .filesystem import (
    FileEntry,
    FileStat,
    FileSystem,
    LocalFileSystem,
)
from .shell import (
    BackgroundHandle,
    LocalShellExecutor,
    ShellExecutor,
    ShellRequest,
    ShellResult,
)

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
]
