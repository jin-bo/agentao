"""Sub-agent tool registration.

Extracted from ``Agentao._register_agent_tools``. Wires the
``AgentManager``-produced sub-agent tools onto ``agent.tools``, with the
session transport as the sink for their events (start/end, per-step tool
calls, output chunks), so the CLI / ACP observe them the same way as
top-level tool calls. The wrapper forwards a whitelist of the child's
events (``AgentToolWrapper._make_forwarder``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ..agents.tools import AgentToolWrapper
from ..llm._usage import accepts_keyword
from ..transport.sdk import invoke_ask_user_callback

if TYPE_CHECKING:
    from ..agent import Agentao


def transport_ask_user(agent: "Agentao") -> Callable[..., str]:
    """An ``ask_user`` callback that reaches ``agent.transport.ask_user``.

    The transport is read per call (replay may splice an adapter in later),
    and the signature check runs against the transport method itself: a
    ``lambda *a, **kw`` here would hide that signature from
    ``invoke_ask_user_callback`` and forward every hint, so a transport
    whose ``ask_user`` takes only the question would raise ``TypeError``.
    """
    def _ask(question: str, **hints: Any) -> str:
        return invoke_ask_user_callback(agent.transport.ask_user, question, hints)
    return _ask


def _add_usage(
    agent: "Agentao", prompt_tokens: int, completion_tokens: int, **cache_counts: int,
) -> None:
    """Add a finished sub-agent's usage to the parent's session totals.

    ``agent.llm`` is read here rather than captured: it is the object a host
    may have injected, and one without ``add_usage`` keeps no totals to add to.
    ``reasoning_tokens`` is passed only when there are some and the host's
    ``add_usage`` takes the keyword. One from before it would raise
    ``TypeError`` and lose the prompt and completion counts with it.
    """
    add = getattr(agent.llm, "add_usage", None)
    if callable(add):
        if not cache_counts.get("reasoning_tokens") or not accepts_keyword(add, "reasoning_tokens"):
            cache_counts.pop("reasoning_tokens", None)
        add(prompt_tokens, completion_tokens, **cache_counts)


def register_agent_tools(agent: "Agentao") -> None:
    """Register sub-agent tools on ``agent.tools``.

    No-op when the agent was constructed without an ``agent_manager``.
    Must run *after* :func:`register_builtin_tools` so the sub-agent
    tools have the full tool catalogue to forward from.
    """
    if agent.agent_manager is None:
        return

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
        # ``close()`` cancels the background tasks launched under this key.
        bg_owner=agent._bg_owner,
        # A foreground sub-agent's events go to the parent's transport as
        # they are, and its confirmations and questions are answered there.
        # Each lambda reads ``agent.transport`` per call: replay replaces it
        # after this registration, and a bound method would keep the old one.
        event_sink=lambda event: agent.transport.emit(event),
        confirm_tool=lambda *a, **kw: agent.transport.confirm_tool(*a, **kw),
        ask_user=transport_ask_user(agent),
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
        # Off unless the agent was built with ``relay_subagent_events=True``.
        subagent_relay=getattr(agent, "_subagent_relay", None),
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
