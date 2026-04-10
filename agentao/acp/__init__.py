"""Agent Client Protocol (ACP) support.

This subpackage implements Agentao's ACP stdio JSON-RPC server. Issue 01
establishes the module skeleton and a working JSON-RPC dispatcher with
correct error handling; later issues layer on ACP method handlers, session
lifecycle, event transport mapping, permissions, and cancellation.

See ``docs/implementation/ACP_GITHUB_EPIC.md`` for the full plan.
"""

from .server import AcpServer

__all__ = ["AcpServer"]
