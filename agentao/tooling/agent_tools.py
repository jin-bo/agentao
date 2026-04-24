"""Sub-agent tool registration.

Extracted from ``Agentao._register_agent_tools``. Wires the
``AgentManager``-produced sub-agent tools onto ``agent.tools`` and
bridges their runtime events (start/end, per-step tool calls, output
chunks) onto the session transport so the CLI / ACP observe them the
same way as top-level tool calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..transport import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao


def register_agent_tools(agent: "Agentao") -> None:
    """Register sub-agent tools on ``agent.tools``.

    No-op when the agent was constructed without an ``agent_manager``.
    Must run *after* :func:`register_builtin_tools` so the sub-agent
    tools have the full tool catalogue to forward from.
    """
    if agent.agent_manager is None:
        return

    # Maps sub-agent tool_name → call_id so TOOL_OUTPUT and TOOL_COMPLETE
    # events carry the same stable key as their matching TOOL_START.
    # Keyed by name — works for serial and different-named parallel calls.
    _subagent_call_ids: dict = {}

    def _agent_step_cb(name, args):
        if name is None:
            agent.transport.emit(AgentEvent(EventType.TURN_START, {}))
        elif name == "__agent_start__":
            agent.transport.emit(AgentEvent(EventType.AGENT_START, {
                "agent": args.agent_name,
                "task": args.task,
                "max_turns": args.max_turns,
            }))
        elif name == "__agent_end__":
            agent.transport.emit(AgentEvent(EventType.AGENT_END, {
                "agent": args.agent_name,
                "state": args.state,
                "turns": args.turns,
                "tool_calls": args.tool_calls,
                "tokens": args.tokens,
                "duration_ms": args.duration_ms,
                "error": args.error,
            }))
        else:
            # call_id is injected by build_compat_transport; fall back to name.
            _args = dict(args) if isinstance(args, dict) else {}
            call_id = _args.pop("__call_id__", None) or name
            _subagent_call_ids[name] = call_id
            agent.transport.emit(AgentEvent(EventType.TOOL_START, {
                "tool": name, "args": _args, "call_id": call_id,
            }))

    agent_tools = agent.agent_manager.create_agent_tools(
        all_tools=agent.tools.tools,
        llm_config=agent._llm_config,
        confirmation_callback=lambda *a, **kw: agent.transport.confirm_tool(*a, **kw),
        step_callback=_agent_step_cb,
        output_callback=lambda name, chunk: agent.transport.emit(
            AgentEvent(EventType.TOOL_OUTPUT, {
                "tool": name, "chunk": chunk,
                "call_id": _subagent_call_ids.get(name, name),
            })
        ),
        tool_complete_callback=lambda name: agent.transport.emit(
            AgentEvent(EventType.TOOL_COMPLETE, {
                "tool": name,
                "call_id": _subagent_call_ids.pop(name, name),
                "status": "ok", "duration_ms": 0, "error": None,
            })
        ),
        ask_user_callback=lambda *a, **kw: agent.transport.ask_user(*a, **kw),
        max_context_tokens=agent.context_manager.max_tokens,
        parent_messages_getter=lambda: agent.messages,
        cancellation_token_getter=lambda: agent._current_token,
        readonly_mode_getter=lambda: getattr(agent, 'tool_runner', None) is not None and agent.tool_runner.readonly_mode,
        permission_mode_getter=lambda: getattr(agent.tool_runner, '_permission_engine', None) and agent.tool_runner._permission_engine.active_mode,
    )
    for agent_tool in agent_tools:
        agent.tools.register(agent_tool)
