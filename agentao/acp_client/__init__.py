"""ACP client — manages connections to project-local ACP servers."""

from .client import ACPClient, AcpClientError, AcpConnectionInfo, AcpRpcError
from .config import load_acp_client_config
from .inbox import Inbox, InboxMessage, MessageKind
from .interaction import InteractionKind, InteractionRegistry, PendingInteraction
from .manager import ACPManager
from .models import (
    AcpClientConfig,
    AcpConfigError,
    AcpProcessInfo,
    AcpServerConfig,
    ServerState,
)
from .process import ACPProcessHandle

__all__ = [
    "ACPClient",
    "ACPManager",
    "ACPProcessHandle",
    "AcpClientConfig",
    "AcpClientError",
    "AcpConfigError",
    "AcpConnectionInfo",
    "AcpProcessInfo",
    "AcpRpcError",
    "AcpServerConfig",
    "Inbox",
    "InboxMessage",
    "InteractionKind",
    "InteractionRegistry",
    "MessageKind",
    "PendingInteraction",
    "ServerState",
    "load_acp_client_config",
]
