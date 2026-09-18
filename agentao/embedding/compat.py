"""Legacy-callback → :class:`Transport` adapter (public migration surface).

When :class:`agentao.agent.Agentao` shipped, the constructor accepted
eight callbacks for incremental UI plumbing
(``confirmation_callback``, ``step_callback``, ``thinking_callback``,
``ask_user_callback``, ``output_callback``, ``tool_complete_callback``,
``llm_text_callback``, ``on_max_iterations_callback``). They predate
the :class:`agentao.transport.Transport` protocol, were deprecated
through 0.4.x, and were removed from ``Agentao.__init__`` in 0.5.0 —
passing one is now a ``TypeError``. This module is what survives.

The migration path for embedded hosts:

1. Build an :class:`agentao.transport.SdkTransport` directly with
   ``on_event=`` / ``confirm_tool=`` / ``ask_user=`` /
   ``on_max_iterations=`` callbacks. New events
   (TURN_BEGIN/END/AGENT_*/etc.) are reachable on this surface.
2. Or, if rewiring the host to consume :class:`AgentEvent` is
   prohibitive, call :func:`build_compat_transport` here to wrap the
   legacy 8-callback API into a single transport, then pass
   ``transport=`` to ``Agentao(...)``.

Both paths route through the public Transport contract.
"""

from ..transport.sdk import build_compat_transport

__all__ = ["build_compat_transport"]
