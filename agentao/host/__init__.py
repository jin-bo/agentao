"""Public host-facing contract package for embedded Agentao runtimes.

``agentao.host`` (renamed from ``agentao.harness`` in 0.4.2 — the
design doc consistently uses "harness" for *Agentao itself running
inside the host*, so the contract package now reads as the surface a
host application talks to) is the stability boundary for hosts
embedding Agentao. It covers three pillars:

* **Observability events** — :class:`ToolLifecycleEvent`,
  :class:`SubagentLifecycleEvent`, :class:`PermissionDecisionEvent`,
  delivered via :class:`EventStream` (``Agentao.events()``).
* **ACP schema surface** — Pydantic models for the host-facing ACP
  payloads, exported via :func:`export_host_acp_json_schema`.
* **Permission state** — :class:`ActivePermissions` snapshot getter
  (``Agentao.active_permissions()``).

It is **not** a complete chat runtime. To drive a turn, use
``Agentao.arun()``. To render streaming chat UI, use the internal
``Transport``/``AgentEvent`` stream or the ACP protocol — those carry
the full assistant text, reasoning, and raw tool I/O that this stable
contract intentionally omits.

Internal runtime types (``AgentEvent``, ``ToolExecutionResult``,
``PermissionEngine``) are intentionally not re-exported. See
``docs/api/host.md`` and ``docs/design/embedded-host-contract.md``
(the "Embedded Harness Contract" design doc — the conceptual word
"harness" still refers to Agentao-as-embedded-runtime; only the package
and the symbols around it were renamed for consistency).

The ``agentao.harness`` import path remains for one minor as a
deprecated alias (with a ``DeprecationWarning`` on first import) and
keeps the old symbol names (``HarnessEvent``, ``HarnessReplaySink``,
``export_harness_*``) wired to the new ones via the shim — so existing
code keeps running until 0.5.0.
"""

from .events import EventStream, StreamSubscribeError
from .models import (
    ActivePermissions,
    HostEvent,
    PermissionDecisionEvent,
    RFC3339UTCString,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)
from .schema import (
    export_host_acp_json_schema,
    export_host_event_json_schema,
)

__all__ = [
    "ActivePermissions",
    "EventStream",
    "HostEvent",
    "PermissionDecisionEvent",
    "RFC3339UTCString",
    "StreamSubscribeError",
    "SubagentLifecycleEvent",
    "ToolLifecycleEvent",
    "export_host_acp_json_schema",
    "export_host_event_json_schema",
]
