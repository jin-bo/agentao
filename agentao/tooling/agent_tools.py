"""Sub-agent tool registration.

Extracted from ``Agentao._register_agent_tools``. Wires the
``AgentManager``-produced sub-agent tools onto ``agent.tools`` and
bridges their runtime events (start/end, per-step tool calls, output
chunks) onto the session transport so the CLI / ACP observe them the
same way as top-level tool calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..agents.tools import AgentToolWrapper
from ..transport import AgentEvent, EventType

if TYPE_CHECKING:
    from ..agent import Agentao


def _add_usage(
    agent: "Agentao", prompt_tokens: int, completion_tokens: int, **cache_counts: int,
) -> None:
    """Add a finished sub-agent's usage to the parent's session totals.

    ``agent.llm`` is read here rather than captured: it is the object a host
    may have injected, and one without ``add_usage`` keeps no totals to add to.
    """
    add = getattr(agent.llm, "add_usage", None)
    if callable(add):
        add(prompt_tokens, completion_tokens, **cache_counts)


def register_agent_tools(agent: "Agentao") -> None:
    """Register sub-agent tools on ``agent.tools``.

    No-op when the agent was constructed without an ``agent_manager``.
    Must run *after* :func:`register_builtin_tools` so the sub-agent
    tools have the full tool catalogue to forward from.
    """
    if agent.agent_manager is None:
        return

    def _agent_step_cb(name, args):
        if name is None:
            agent.transport.emit(AgentEvent(EventType.TURN_START, (
                {"display_reset": True}
                if isinstance(args, dict) and args.get("display_reset") else {}
            )))
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
            agent.transport.emit(AgentEvent(EventType.TOOL_START, {
                "tool": name, "args": _args, "call_id": call_id,
            }))

    # TOOL_OUTPUT / TOOL_COMPLETE receive the stable ``call_id`` (and, for
    # completion, the real status / duration / error) forwarded by
    # build_compat_transport, so a same-named parallel batch (e.g. four
    # concurrent ``read_file``) stays correlatable with its matching
    # TOOL_START *and* a failed or permission-denied sub-agent tool is
    # reported as a failure rather than a hardcoded success. The tool-name
    # fallback fires only when the id is genuinely absent (``None``) — an
    # empty-string id is preserved so it is not silently collapsed.
    def _agent_output_cb(name, chunk, call_id=None):
        agent.transport.emit(AgentEvent(EventType.TOOL_OUTPUT, {
            "tool": name, "chunk": chunk,
            "call_id": call_id if call_id is not None else name,
        }))

    def _agent_tool_complete_cb(
        name, call_id=None, status=None, duration_ms=None, error=None,
    ):
        agent.transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
            "tool": name,
            "call_id": call_id if call_id is not None else name,
            "status": status if status is not None else "ok",
            "duration_ms": duration_ms if duration_ms is not None else 0,
            "error": error,
        }))

    agent_tools = agent.agent_manager.create_agent_tools(
        all_tools=agent.tools.tools,
        # How a sub-agent tells the parent's built-ins from host tools that
        # replaced them, including one of the built-in's own class (#256).
        tool_origin_getter=lambda name: agent.tools.origin(name),
        # Live getter — sub-agents launched after a runtime
        # ``session/set_model`` / maxTokens change pick up the new
        # values rather than the snapshot frozen at registration time.
        llm_config_getter=lambda: agent._llm_config,
        bg_store=agent.bg_store,
        confirmation_callback=lambda *a, **kw: agent.transport.confirm_tool(*a, **kw),
        confirmation_event_callback=lambda event: agent.transport.emit(event),
        step_callback=_agent_step_cb,
        output_callback=_agent_output_cb,
        tool_complete_callback=_agent_tool_complete_cb,
        ask_user_callback=lambda *a, **kw: agent.transport.ask_user(*a, **kw),
        max_context_tokens=agent.context_manager.max_tokens,
        parent_messages_getter=lambda: agent.messages,
        cancellation_token_getter=lambda: agent._current_token,
        # The runner's flag *or* its engine's mode, as the parent's own gate
        # reads it: a parent put in read-only through the engine alone (ACP
        # ``session/set_mode``, a host's ``set_mode``) sets no flag.
        readonly_mode_getter=lambda: getattr(agent, 'tool_runner', None) is not None and agent.tool_runner.readonly_active(),
        permission_engine_getter=lambda: getattr(agent.tool_runner, '_permission_engine', None),
        # Live getter: in the CLI a plugin's skills are registered onto this
        # manager *after* the agent is built, so a sub-agent spawned later
        # has to read it at spawn to see them (#254).
        skill_manager_getter=lambda: getattr(agent, "skill_manager", None),
        # A sub-agent has its own LLM client, so its requests reach the
        # session totals — and ``agentao run``'s usage — only through this.
        # Read at call time: a host-injected ``llm_client`` may not have the
        # method, and then there is nothing to add to.
        usage_sink=lambda prompt, completion, **cache: _add_usage(agent, prompt, completion, **cache),
        sandbox_policy=getattr(agent, "sandbox_policy", None),
        subagent_emitter=getattr(agent, "_host_subagent_emitter", None),
        # The backends the parent's built-ins were bound to, so a sub-agent's
        # file and shell calls run where the parent's do.
        filesystem=getattr(agent, "filesystem", None),
        shell=getattr(agent, "shell", None),
    )
    for agent_tool in agent_tools:
        # ``create_agent_tools`` also returns ``check_background_agent`` when a
        # ``bg_store`` is set. That one is a built-in (``register_builtin_tools``
        # already registered it), and sub-agents get their own — so it is tagged
        # ``builtin``, and its collision with that earlier registration is the
        # expected one, not an accident. Without ``replace=`` the guaranteed
        # collision logged an "already registered; overwriting" warning on every
        # construction — once per sub-agent spawn too — which is how a warning
        # that exists to surface *accidental* collisions became noise nobody
        # reads. Only ``AgentToolWrapper``s get ``agent``.
        origin = "agent" if isinstance(agent_tool, AgentToolWrapper) else "builtin"
        agent.tools.register(
            agent_tool,
            origin=origin,
            replace=agent_tool.name in agent.tools.tools,
        )
