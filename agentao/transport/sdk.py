"""SdkTransport — programmatic transport backed by optional callbacks.

Also provides ``build_compat_transport`` to wrap the legacy 8-callback
API that existed before the Transport abstraction was introduced.
"""

import inspect
import warnings
from typing import Any, Callable, Dict, List, Optional

from .broadcast import EventBroadcaster
from .events import AgentEvent, EventType
from .null import NullTransport


def invoke_ask_user_callback(callback: Callable[..., str], question: str, structured: Dict[str, Any]) -> str:
    """Call a user-supplied ``ask_user`` callback, forwarding structured
    kwargs only when the callback can accept them.

    Shared by :class:`SdkTransport` and :class:`agentao.tools.ask_user.AskUserTool`
    so both honour the same backward-compatibility rule: a legacy 1-arg
    ``Callable[[str], str]`` callback (whose signature names none of the
    structured fields and has no ``**kwargs``) is called with the question
    alone, so the structured hints are silently dropped rather than raising
    ``TypeError``.
    """
    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        # Un-introspectable callable (some builtins / C funcs) — assume legacy.
        return callback(question)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return callback(question, **structured)
    # Only forward a field the callback can actually accept *by keyword* —
    # a positional-only parameter that happens to share a name would raise
    # TypeError if passed as a keyword.
    keyword_ok = {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }
    accepted = {
        k: v
        for k, v in structured.items()
        if k in params and params[k].kind in keyword_ok
    }
    return callback(question, **accepted)


class SdkTransport:
    """A transport driven by optional Python callbacks.

    Suitable for embedding Agentao in scripts, tests, or other programs
    that want structured events without a terminal UI.

    All callback parameters are optional; unset ones fall back to the
    ``NullTransport`` behaviour (ignore / auto-approve).

    The ``on_event`` callback receives the live :class:`AgentEvent`
    dataclass so embedded callers can branch on ``event.type`` cheaply.
    Hosts that need to forward events over a wire protocol (SSE,
    WebSocket, IPC) should call :meth:`AgentEvent.to_dict` to get the
    versioned ``{"type", "schema_version", "data"}`` payload.

    Example::

        events = []
        transport = SdkTransport(on_event=events.append)
        agent = Agentao(transport=transport)
        agent.chat("hello")

        # Wire form (versioned by AgentEvent.schema_version):
        wire = [e.to_dict() for e in events]
    """

    def __init__(
        self,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        confirm_tool: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        ask_user: Optional[Callable[..., str]] = None,
        on_max_iterations: Optional[Callable[[int, List], dict]] = None,
    ) -> None:
        self._on_event = on_event
        self._confirm_tool = confirm_tool
        self._ask_user = ask_user
        self._on_max_iterations = on_max_iterations
        self._broadcast = EventBroadcaster()

    # ── Transport protocol ────────────────────────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass  # never let a callback crash the runtime
        self._broadcast.notify(event)

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable[[], None]:
        return self._broadcast.subscribe(listener)

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        if self._confirm_tool:
            return self._confirm_tool(tool_name, description, args)
        return True  # auto-approve when no callback

    def ask_user(
        self,
        question: str,
        *,
        header: Optional[str] = None,
        options: Optional[List[str]] = None,
        multiple: bool = False,
        allow_custom: bool = True,
    ) -> str:
        if self._ask_user:
            return invoke_ask_user_callback(
                self._ask_user,
                question,
                {
                    "header": header,
                    "options": options,
                    "multiple": multiple,
                    "allow_custom": allow_custom,
                },
            )
        return "[ask_user: not available in non-interactive mode]"

    def on_max_iterations(self, count: int, messages: list) -> dict:
        if self._on_max_iterations:
            return self._on_max_iterations(count, messages)
        return {"action": "stop"}


# ── Backward-compatibility shim ───────────────────────────────────────────────

_NULL = NullTransport()


def _accepted_meta_keywords(
    callback: Optional[Callable[..., Any]], candidates: tuple
) -> tuple:
    """Return the subset of ``candidates`` that ``callback`` accepts *by name*.

    The legacy ``tool_complete_callback`` / ``output_callback`` signatures
    were ``(name)`` and ``(name, chunk)`` — they had no channel for the
    per-call id (or status / duration / error), so a bridge consuming them
    had to reconstruct the id from the tool *name*, which collapses for a
    parallel batch of same-named tool calls (e.g. four concurrent
    ``read_file``). A callback opts into the richer metadata simply by naming
    the parameter (``call_id``, ``status``, …); we forward only the keywords
    it explicitly declares as ``POSITIONAL_OR_KEYWORD`` / ``KEYWORD_ONLY``,
    so a genuinely legacy fixed-arity callback keeps its exact old behaviour
    and never sees an unexpected keyword.

    ``**kwargs`` is deliberately *not* treated as opt-in: the bridge passes
    ``name`` / ``chunk`` positionally (the legacy contract), which a
    ``**kwargs``-only callable cannot accept, so green-lighting it would only
    raise a ``TypeError`` that ``SdkTransport.emit`` swallows — i.e. forward
    nothing rather than silently drop the call.

    Computed once per ``build_compat_transport`` (callbacks are stable for a
    transport's lifetime), so per-event dispatch does no signature work.
    """
    if callback is None:
        return ()
    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        # Un-introspectable callable (some builtins / C funcs) — assume legacy.
        return ()
    keyword_ok = {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }
    return tuple(
        c for c in candidates
        if c in params and params[c].kind in keyword_ok
    )


def build_compat_transport(
    confirmation_callback=None,
    step_callback=None,
    thinking_callback=None,
    ask_user_callback=None,
    output_callback=None,
    tool_complete_callback=None,
    llm_text_callback=None,
    on_max_iterations_callback=None,
) -> "SdkTransport":
    """Wrap the legacy 8-callback API into a single ``SdkTransport``.

    Called automatically by ``Agentao.__init__`` when old-style callbacks
    are passed without a ``transport`` argument.  All parameters are optional.
    """
    # Decide once (not per-event) which metadata keywords each callback can
    # receive, so a same-named parallel tool batch stays correlatable
    # end-to-end (call_id) and a sub-agent tool failure is reported as a
    # failure rather than collapsing onto the tool name / a hardcoded "ok".
    _out_kw = _accepted_meta_keywords(output_callback, ("call_id",))
    _tc_kw = _accepted_meta_keywords(
        tool_complete_callback, ("call_id", "status", "duration_ms", "error"),
    )

    def _on_event(event: AgentEvent) -> None:
        t = event.type
        d = event.data
        if t == EventType.TURN_START:
            if step_callback:
                step_callback(None, {})
        elif t == EventType.TOOL_START:
            if step_callback:
                # Inject call_id into args under a private key so the parent
                # agent's step_callback can recover it and emit a call_id-keyed
                # TOOL_START event for the DisplayController.
                _call_id = d.get("call_id")
                _args = dict(d.get("args", {}))
                if _call_id:
                    _args["__call_id__"] = _call_id
                step_callback(d.get("tool"), _args)
        elif t == EventType.TOOL_OUTPUT:
            if output_callback:
                output_callback(
                    d.get("tool", ""), d.get("chunk", ""),
                    **{k: d.get(k) for k in _out_kw},
                )
        elif t == EventType.TOOL_COMPLETE:
            if tool_complete_callback:
                tool_complete_callback(
                    d.get("tool", ""),
                    **{k: d.get(k) for k in _tc_kw},
                )
        elif t == EventType.THINKING:
            if thinking_callback:
                thinking_callback(d.get("text", ""))
        elif t == EventType.LLM_TEXT:
            if llm_text_callback:
                llm_text_callback(d.get("chunk", ""))
        elif t == EventType.AGENT_START:
            # Map back to magic-string step_callback for legacy callers
            if step_callback:
                from ..agents.tools import SubagentProgress
                step_callback("__agent_start__", SubagentProgress(
                    agent_name=d.get("agent", ""),
                    state="running",
                    task=d.get("task", ""),
                    max_turns=d.get("max_turns", 0),
                    turns=0, tool_calls=0, tokens=0, duration_ms=0,
                    result=None, error=None,
                ))
        elif t == EventType.AGENT_END:
            if step_callback:
                from ..agents.tools import SubagentProgress
                step_callback("__agent_end__", SubagentProgress(
                    agent_name=d.get("agent", ""),
                    state=d.get("state", "completed"),
                    task="",
                    max_turns=0,
                    turns=d.get("turns", 0),
                    tool_calls=d.get("tool_calls", 0),
                    tokens=d.get("tokens", 0),
                    duration_ms=d.get("duration_ms", 0),
                    result=None,
                    error=d.get("error"),
                ))

    def _confirm(name: str, desc: str, args: dict) -> bool:
        if confirmation_callback:
            return confirmation_callback(name, desc, args)
        return True

    def _ask(question: str) -> str:
        if ask_user_callback:
            return ask_user_callback(question)
        return "[ask_user: not available in non-interactive mode]"

    def _max_iter(count: int, messages: list) -> dict:
        if on_max_iterations_callback:
            return on_max_iterations_callback(count, messages)
        return {"action": "stop"}

    return SdkTransport(
        on_event=_on_event,
        confirm_tool=_confirm,
        ask_user=_ask,
        on_max_iterations=_max_iter,
    )
