"""Public host-facing contract package for embedded Agentao runtimes.

``agentao.host`` (renamed from ``agentao.harness`` in 0.4.2 — the
design doc consistently uses "harness" for *Agentao itself running
inside the host*, so the contract package now reads as the surface a
host application talks to) is the stability boundary for hosts
embedding Agentao. It covers four pillars:

* **Observability events** — :class:`ToolLifecycleEvent`,
  :class:`SubagentLifecycleEvent`, :class:`PermissionDecisionEvent`,
  delivered via :class:`EventStream` (``Agentao.events()``).
* **ACP schema surface** — Pydantic models for the host-facing ACP
  payloads, exported via :func:`export_host_acp_json_schema`.
* **Permission state** — :class:`ActivePermissions` snapshot getter
  (``Agentao.active_permissions()``).
* **Streaming text** — :class:`TextDelta` and :class:`TurnOutcome`, the
  items of ``Agentao.astream()``. They are a delivery API, not events:
  they are not ``HostEvent`` members and are not projected into replay.

It also re-exports :class:`CancellationToken` (the same class as
``agentao.cancellation.CancellationToken``) for ``chat()`` / ``arun()`` /
``astream()``'s ``cancellation_token=``, and the tool base classes, lazily.
``TurnOutcome`` is the same class as ``agentao.TurnOutcome``.

It is **not** a complete chat runtime. To drive a turn, use
``Agentao.arun()`` or ``Agentao.astream()``. Reasoning text and raw tool
I/O stay outside this contract; a host that needs them uses the internal
``Transport``/``AgentEvent`` stream or the ACP protocol.

Internal runtime types (``AgentEvent``, ``ToolExecutionResult``,
``PermissionEngine``) are intentionally not re-exported. See
``docs/reference/host-api.md`` and ``docs/design/embedded-host-contract.md``
(the "Embedded Harness Contract" design doc — the conceptual word
"harness" still refers to Agentao-as-embedded-runtime; only the package
and the symbols around it were renamed for consistency).

The ``agentao.harness`` alias package — the old import path and the old
symbol names (``HarnessEvent``, ``HarnessReplaySink``,
``export_harness_*``) — was removed in 0.5.0 after warning since 0.4.2.
"""

from ..cancellation import CancellationToken
from ..outcome import TurnOutcome
from .events import EventStream, StreamSubscribeError
from .models import (
    ActivePermissions,
    HostEvent,
    PermissionDecisionEvent,
    RFC3339UTCString,
    SubagentLifecycleEvent,
    SubagentUsage,
    ToolLifecycleEvent,
)
from typing import Any, TYPE_CHECKING

from .stream import TextDelta
from .schema import (
    export_host_acp_json_schema,
    export_host_event_json_schema,
)

# Tool base types for host ``extra_tools`` injection are re-exported
# *lazily* (PEP 562 ``__getattr__``). Importing them eagerly would pull the
# entire ``agentao.tools`` package — ~35 modules (mcp, sandbox, capabilities
# …) — onto every ``import agentao.host``, defeating the lightweight
# stability-boundary intent (a host importing only ``HostEvent`` /
# ``ActivePermissions`` typing should not drag in the tool runtime). The
# canonical definitions stay in ``agentao.tools.base``; this is a stable
# import path, NOT a new host-tool abstraction layer.
_LAZY_TOOL_EXPORTS = frozenset({"AsyncToolBase", "RegistrableTool", "Tool"})

if TYPE_CHECKING:  # static type-checkers resolve the names directly
    from ..tools.base import AsyncToolBase, RegistrableTool, Tool


def __getattr__(name: str) -> Any:
    if name in _LAZY_TOOL_EXPORTS:
        from ..tools import base as _base  # pulls the tools package once, on demand
        return getattr(_base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # Without this, ``dir(agentao.host)`` omits every lazy export — the
    # module-level ``__getattr__`` is invisible to ``dir``. Listing names
    # imports nothing. Same pattern as ``agentao/__init__.py``.
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "ActivePermissions",
    "AsyncToolBase",
    "CancellationToken",
    "EventStream",
    "HostEvent",
    "PermissionDecisionEvent",
    "RFC3339UTCString",
    "RegistrableTool",
    "StreamSubscribeError",
    "SubagentLifecycleEvent",
    "SubagentUsage",
    "TextDelta",
    "Tool",
    "ToolLifecycleEvent",
    "TurnOutcome",
    "export_host_acp_json_schema",
    "export_host_event_json_schema",
]
