"""Base tool classes."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..capabilities import FileSystem, ShellExecutor

_logger = logging.getLogger(__name__)


class Tool(ABC):
    """Base class for all tools."""

    def __init__(self):
        self.output_callback: Optional[Callable[[str], None]] = None
        # Per-session working directory bound by Agentao at registration time.
        # ``None`` = legacy behavior: relative paths resolve against the
        # process cwd at call time. A ``Path`` binds the tool to a specific
        # session cwd so two ACP sessions with different cwd values do not
        # leak state through relative file paths.
        self.working_directory: Optional[Path] = None
        # Capability injection. ``None`` means "construct a local default
        # lazily at first use"; embedded hosts override at registration
        # time to redirect IO through Docker exec, virtual FS, audit
        # proxy, etc.
        self.filesystem: Optional["FileSystem"] = None
        self.shell: Optional["ShellExecutor"] = None

    def _get_fs(self) -> "FileSystem":
        """Lazy accessor — build a ``LocalFileSystem`` on first use."""
        if self.filesystem is None:
            from ..capabilities import LocalFileSystem
            self.filesystem = LocalFileSystem()
        return self.filesystem

    def _get_shell(self) -> "ShellExecutor":
        """Lazy accessor — build a ``LocalShellExecutor`` on first use."""
        if self.shell is None:
            from ..capabilities import LocalShellExecutor
            self.shell = LocalShellExecutor()
        return self.shell

    # ------------------------------------------------------------------
    # Path resolution helpers (Issue 05)
    # ------------------------------------------------------------------

    def _resolve_path(self, raw: str) -> Path:
        """Resolve a user-supplied path against this tool's working directory.

        - ``~`` is expanded.
        - Absolute paths pass through unchanged.
        - Relative paths are joined to ``self.working_directory`` if set;
          otherwise returned as a relative ``Path`` (legacy: ``open()`` and
          friends resolve them against the process cwd).

        Deliberately does NOT call ``.resolve()`` on the result — we preserve
        the path the caller supplied so error messages stay readable. If a
        caller needs the canonical absolute path, they can call ``.resolve()``
        themselves.
        """
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
        if self.working_directory is not None:
            return self.working_directory / p
        return p

    def _resolve_directory(self, raw: str) -> Path:
        """Like :meth:`_resolve_path` but always returns a resolved absolute path.

        Shell and search tools need the canonical path because they pass it
        to subprocesses via ``cwd=`` and use it for ``path.relative_to``
        computations. Uses ``.resolve()`` so symlinks are followed once.
        """
        p = Path(raw).expanduser()
        if not p.is_absolute() and self.working_directory is not None:
            p = self.working_directory / p
        return p.resolve()

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """Tool parameters schema (JSON Schema)."""
        pass

    @property
    def requires_confirmation(self) -> bool:
        """Whether this tool requires user confirmation before execution."""
        return False

    @property
    def is_read_only(self) -> bool:
        """Whether this tool only reads data and never modifies state.

        Read-only tools (read_file, glob, search_file_content, etc.) can be
        safely skipped in future plan-mode enforcement and used to inform
        smarter confirmation policies.  Override and return True in tools that
        never write files, run commands, or mutate external state.
        """
        return False

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Execute the tool with given parameters.

        Args:
            **kwargs: Tool parameters

        Returns:
            Tool execution result as string
        """
        pass

    def to_openai_format(self) -> Dict[str, Any]:
        """Convert tool to OpenAI function format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Registry for managing tools."""

    def __init__(self):
        self.tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool.

        Logs a warning if a tool with the same name is already registered, so
        accidental MCP / built-in name collisions are visible in agentao.log.
        """
        if tool.name in self.tools:
            _logger.warning(
                "Tool '%s' is already registered; overwriting with %s",
                tool.name,
                type(tool).__name__,
            )
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Get a tool by name.

        Raises:
            KeyError: with a descriptive message listing available tools.
        """
        if name not in self.tools:
            available = ", ".join(sorted(self.tools)) or "<none>"
            raise KeyError(
                f"Tool '{name}' not found. Available tools: {available}"
            )
        return self.tools[name]

    def list_tools(self) -> List[Tool]:
        """List all registered tools."""
        return list(self.tools.values())

    # Tools that are only exposed to the model when plan mode is active.
    # Centralised here so the chat loop, token accounting, and tests stay in sync.
    _PLAN_ONLY_TOOLS = frozenset({"plan_save", "plan_finalize"})

    def to_openai_format(self, *, plan_mode: bool = False) -> List[Dict[str, Any]]:
        """Convert all tools to OpenAI function-calling format.

        Args:
            plan_mode: When ``False`` (default), plan-only tools (``plan_save``,
                ``plan_finalize``) are omitted from the schema. The chat loop
                passes the current plan-session state so the model only sees
                these tools while plan mode is active.
        """
        return [
            tool.to_openai_format()
            for tool in self.tools.values()
            if plan_mode or tool.name not in self._PLAN_ONLY_TOOLS
        ]
