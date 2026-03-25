"""Base tool classes."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


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
        """Whether this tool requires user confirmation before execution.

        Returns:
            True if confirmation is required, False otherwise
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
        """Convert tool to OpenAI function format.

        Returns:
            Tool definition in OpenAI format
        """
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
        """Initialize tool registry."""
        self.tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool.

        Args:
            tool: Tool to register
        """
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance

        Raises:
            KeyError: If tool not found
        """
        return self.tools[name]

    def list_tools(self) -> List[Tool]:
        """List all registered tools.

        Returns:
            List of tools
        """
        return list(self.tools.values())

    def to_openai_format(self) -> List[Dict[str, Any]]:
        """Convert all tools to OpenAI format.

        Returns:
            List of tool definitions
        """
        return [tool.to_openai_format() for tool in self.tools.values()]
