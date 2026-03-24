"""MCP client and client manager for connecting to MCP servers."""

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.types import Tool as McpToolDef

from .config import McpServerConfig

logger = logging.getLogger("chatagent.mcp")


class ServerStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class McpClient:
    """Manages a single MCP server connection."""

    def __init__(self, name: str, config: McpServerConfig):
        self.name = name
        self.config = config
        self.status = ServerStatus.DISCONNECTED
        self.error_message: Optional[str] = None
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._tools: List[McpToolDef] = []

    @property
    def transport_type(self) -> str:
        if self.config.get("command"):
            return "stdio"
        if self.config.get("url"):
            return "sse"
        return "unknown"

    @property
    def tools(self) -> List[McpToolDef]:
        return self._tools

    @property
    def is_trusted(self) -> bool:
        return bool(self.config.get("trust", False))

    async def connect(self) -> None:
        """Connect to the MCP server and discover tools."""
        self.status = ServerStatus.CONNECTING
        self.error_message = None

        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            if self.config.get("command"):
                await self._connect_stdio()
            elif self.config.get("url"):
                await self._connect_sse()
            else:
                raise ValueError(f"No transport configured for server '{self.name}' (need 'command' or 'url')")

            # Initialize the session
            await self._session.initialize()

            # Discover tools
            result = await self._session.list_tools()
            self._tools = result.tools

            self.status = ServerStatus.CONNECTED
            logger.info(f"MCP server '{self.name}' connected via {self.transport_type}, {len(self._tools)} tools")

        except Exception as e:
            self.status = ServerStatus.ERROR
            self.error_message = str(e)
            logger.error(f"Failed to connect to MCP server '{self.name}': {e}")
            # Cleanup on failure
            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass
                self._exit_stack = None

    async def _connect_stdio(self) -> None:
        """Establish stdio transport."""
        command = self.config["command"]
        args = self.config.get("args", [])

        # Build environment: sanitized base + explicit env vars
        env = dict(os.environ)
        if self.config.get("env"):
            env.update(self.config["env"])

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
            cwd=self.config.get("cwd"),
        )

        stdio_transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = stdio_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def _connect_sse(self) -> None:
        """Establish SSE transport."""
        url = self.config["url"]
        headers = self.config.get("headers", {})
        timeout = self.config.get("timeout", 60)

        sse_transport = await self._exit_stack.enter_async_context(
            sse_client(url, headers=headers, timeout=timeout)
        )
        read_stream, write_stream = sse_transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on this server and return the result as text."""
        if not self._session or self.status != ServerStatus.CONNECTED:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")

        result = await self._session.call_tool(tool_name, arguments)

        # Convert result content to text
        parts = []
        for block in result.content:
            if block.type == "text":
                parts.append(block.text)
            elif block.type == "image":
                parts.append(f"[image: {block.mimeType}]")
            elif block.type == "resource":
                text = getattr(block.resource, "text", None)
                if text:
                    parts.append(text)
                else:
                    parts.append(f"[resource: {getattr(block.resource, 'uri', 'unknown')}]")
            else:
                parts.append(f"[{block.type}]")

        text = "\n".join(parts)

        if result.isError:
            return f"MCP tool error: {text}"
        return text

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._exit_stack:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error disconnecting from MCP server '{self.name}': {e}")
            self._exit_stack = None
        self._session = None
        self._tools = []
        self.status = ServerStatus.DISCONNECTED


class McpClientManager:
    """Manages multiple MCP server connections with sync-async bridge."""

    def __init__(self, server_configs: Dict[str, McpServerConfig]):
        self._configs = server_configs
        self._clients: Dict[str, McpClient] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create a dedicated event loop for MCP operations."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        loop = self._get_loop()
        return loop.run_until_complete(coro)

    @property
    def clients(self) -> Dict[str, McpClient]:
        return self._clients

    @property
    def server_configs(self) -> Dict[str, McpServerConfig]:
        return self._configs

    def connect_all(self) -> None:
        """Connect to all configured MCP servers."""
        if not self._configs:
            return
        self._run(self._connect_all_async())

    async def _connect_all_async(self) -> None:
        """Connect to all servers concurrently."""
        for name, config in self._configs.items():
            client = McpClient(name, config)
            self._clients[name] = client
            try:
                await client.connect()
            except Exception as e:
                logger.error(f"Failed to start MCP server '{name}': {e}")

    def get_client(self, name: str) -> Optional[McpClient]:
        return self._clients.get(name)

    def get_all_tools(self) -> List[Tuple[str, McpToolDef]]:
        """Get all tools from all connected servers.

        Returns:
            List of (server_name, tool_definition) tuples.
        """
        tools = []
        for name, client in self._clients.items():
            if client.status == ServerStatus.CONNECTED:
                for tool in client.tools:
                    tools.append((name, tool))
        return tools

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on a specific server (sync wrapper)."""
        client = self._clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP server '{server_name}' not found")
        return self._run(client.call_tool(tool_name, arguments))

    def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        if self._clients:
            self._run(self._disconnect_all_async())
        if self._loop and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    async def _disconnect_all_async(self) -> None:
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()

    def get_server_status(self) -> List[Dict[str, Any]]:
        """Get status summary of all servers."""
        result = []
        for name, client in self._clients.items():
            result.append({
                "name": name,
                "status": client.status.value,
                "transport": client.transport_type,
                "tools": len(client.tools),
                "trusted": client.is_trusted,
                "error": client.error_message,
            })
        return result
