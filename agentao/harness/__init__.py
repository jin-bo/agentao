"""Public host-facing harness contract for embedded Agentao runtimes.

This package exposes the stable API surface for embedding Agentao:
typed payload models for permissions and lifecycle events, the public
:class:`EventStream` consumed via ``Agentao.events()``, and schema
export helpers that protect those models in release snapshots.

Internal runtime types (``AgentEvent``, ``ToolExecutionResult``,
``PermissionEngine``) are intentionally not re-exported — the harness
package is the compatibility boundary. See ``docs/api/harness.md``.
"""

from .events import EventStream, StreamSubscribeError
from .models import (
    ActivePermissions,
    HarnessEvent,
    PermissionDecisionEvent,
    RFC3339UTCString,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)
from .schema import (
    export_harness_acp_json_schema,
    export_harness_event_json_schema,
)

__all__ = [
    "ActivePermissions",
    "EventStream",
    "HarnessEvent",
    "PermissionDecisionEvent",
    "RFC3339UTCString",
    "StreamSubscribeError",
    "SubagentLifecycleEvent",
    "ToolLifecycleEvent",
    "export_harness_acp_json_schema",
    "export_harness_event_json_schema",
]
