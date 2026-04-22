"""ACP client — stable embedding surface for project-local ACP servers.

The symbols listed in :data:`__all__` form the public embedding contract.
Additional names (``ACPClient``, ``ACPProcessHandle``, ``Inbox``,
``InteractionRegistry``, ``AcpConnectionInfo``, router / render helpers,
etc.) are re-exported for backward compatibility with pre-existing
embedders but are considered implementation details — prefer importing
them from their concrete submodule (``agentao.acp_client.client`` and so
on). Internal imports may change without notice.

Preferred submodule locations for the internal symbols:

- ``ACPClient``                   -> ``agentao.acp_client.client.ACPClient``
- ``ACPProcessHandle``            -> ``agentao.acp_client.process.ACPProcessHandle``
- ``AcpConnectionInfo``           -> ``agentao.acp_client.client.AcpConnectionInfo``
- ``Inbox``, ``InboxMessage``,
  ``MessageKind``                 -> ``agentao.acp_client.inbox``
- ``InteractionKind``,
  ``InteractionRegistry``,
  ``PendingInteraction``          -> ``agentao.acp_client.interaction``
- ``AcpExplicitRoute``,
  ``detect_explicit_route``       -> ``agentao.acp_client.router``
"""

from .client import (
    ACPClient,
    AcpClientError,
    AcpConnectionInfo,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpRpcError,
)
from .config import load_acp_client_config
from .inbox import Inbox, InboxMessage, MessageKind
from .interaction import InteractionKind, InteractionRegistry, PendingInteraction
from .manager import ACPManager
from .models import (
    AcpClientConfig,
    AcpConfigError,
    AcpProcessInfo,
    AcpServerConfig,
    INTERACTION_POLICY_MODES,
    InteractionPolicy,
    PromptResult,
    ServerState,
    ServerStatus,
    classify_process_death,
)
from .process import ACPProcessHandle
from .router import AcpExplicitRoute, detect_explicit_route

__all__ = [
    "ACPClient",
    "ACPManager",
    "ACPProcessHandle",
    "AcpClientConfig",
    "AcpClientError",
    "AcpConfigError",
    "AcpConnectionInfo",
    "AcpErrorCode",
    "AcpExplicitRoute",
    "AcpInteractionRequiredError",
    "AcpProcessInfo",
    "AcpRpcError",
    "AcpServerConfig",
    "INTERACTION_POLICY_MODES",
    "Inbox",
    "InboxMessage",
    "InteractionKind",
    "InteractionPolicy",
    "InteractionRegistry",
    "MessageKind",
    "PendingInteraction",
    "PromptResult",
    "ServerState",
    "ServerStatus",
    "classify_process_death",
    "detect_explicit_route",
    "load_acp_client_config",
]
