"""Base tool classes."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

_logger = logging.getLogger(__name__)


class Tool(ABC):
    """Base class for all tools."""

    def __init__(self):
        self.output_callback: Optional[Callable[[str], None]] = None

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

    def to_openai_format(self) -> List[Dict[str, Any]]:
        """Convert all tools to OpenAI function-calling format."""
        return [tool.to_openai_format() for tool in self.tools.values()]
