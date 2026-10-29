"""``AgentToolWrapper`` — wraps an agent definition as a callable Tool.

The largest piece of the sub-agent system: turns a YAML-style agent
definition (name + description + system prompt + scoped tools + max
turns) into something the parent LLM can invoke via OpenAI function
calling. The wrapper's ``execute`` decides sync vs background dispatch,
builds the parent-context block, scopes the sub-agent's ToolRegistry,
spawns a fresh :class:`Agentao` for the sub-task, and emits public
``SubagentLifecycleEvent`` pairs so hosts can observe lineage.

The class stays in one file because its sub-helpers (parent-context
builder, sync runner, background launcher, child-event forwarder) all read
the same constructor-captured entry points. The free functions that read none
of that state live beside it: what a sub-agent inherits in ``_inherit``,
how its run is classified in ``_outcome``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...cancellation import AgentCancelledError, CancellationToken
from ...llm._usage import accepts_keyword, usage_totals
from ...tools.base import SHELL_TOOL_NAME, RegistrableTool, Tool
from ...transport.events import AgentEvent, EventType
from ...transport.sdk import SdkTransport
from ..bg_store import BackgroundTaskStore
from ._complete import CompleteTaskTool, TaskComplete
from ._inherit import (
    _PARENT_MEMORY_TARGET_TOOL,
    _bind_parent_memory_target,
    _child_memory_manager,
    _child_skill_manager,
    _copy_declared_host_tool,
)
from ._outcome import (
    _classify_subagent_outcome,
    _find_task_complete_result,
    _is_harness_notice,
    _terminal_state,
)

logger = logging.getLogger(__name__)


_READ_RESOURCE_TOOL = "read_mcp_resource"


def _session_mcp_origins(sub_agent: Any) -> set:
    """MCP skill origins of the conversation a sub-agent ran in.

    The child's own transcript does not show everything it saw: its
    ``parent_context`` is the parent's recent messages flattened into a user
    message, and the parent's loaded skills are not tool results there. The
    conversation's loaded (and restored-summary) origins, read at the
    child's own generation, cover what it inherited. Never raises.
    """
    try:
        manager = getattr(sub_agent, "skill_manager", None)
        # Restored without a Skills session to gate with (see
        # ``embedding/sessions.py``): inherited by the child all the same.
        orphan = getattr(manager, "mcp_orphan_origins", None)
        origins = set(orphan) if isinstance(orphan, set) else set()
        mcp_skills = getattr(manager, "mcp_skills", None)
        if mcp_skills is not None:
            origins |= set(mcp_skills.origins(getattr(manager, "mcp_view", None)))
        return origins
    except Exception:  # pragma: no cover - defensive
        return set()


def _without_read_hint(tool: Any) -> Any:
    """``tool`` with its resource-read hint off, if it is an ``McpTool``.

    ``isinstance``, never a ``hasattr`` probe (a mock answers any attribute).
    Imported here, not at module load: only reached when MCP tools exist, so
    the SDK is already loaded.
    """
    from ...mcp.tool import McpTool

    return tool.without_read_hint() if isinstance(tool, McpTool) else tool


def _indent_continuation(text: str) -> str:
    """Indent every line after the first, so quoted text cannot start a line.

    The parent context is one block in the child's ``user`` message, one
    ``[role]: …`` line per message, followed by ``[Your Task]``. An excerpt
    keeping its own line breaks could open a line with ``[user]:`` or
    ``[Your Task]`` — a ``web_fetch`` result could write the child a second
    task. Indented, it reads as a continuation of the message it came from.
    The origin marker is appended after this, so it stays a whole line.
    ``splitlines``, not ``split("\\n")``: ``\\r``, U+2028 and the other
    separators it knows break a line for a reader too.
    """
    return "\n    ".join(text.splitlines())


class AgentToolWrapper(Tool):
    """Wraps an agent definition as a callable Tool for the parent LLM."""

    # How many recent parent messages to inject as context for sub-agents
    PARENT_CONTEXT_MESSAGES = 10

    # Read by the executor on the class, and only ``True`` counts: it then
    # sets ``_parent_tool_call_id`` before each call (#580).
    _takes_parent_tool_call_id = True

    @property
    def is_read_only(self) -> bool:
        # The wrapper itself is always allowed; readonly enforcement is propagated
        # into the sub-agent's own ToolRunner via readonly_mode_getter.
        return True

    def __init__(
        self,
        definition: Dict[str, Any],
        all_tools: Dict[str, RegistrableTool],
        llm_config_getter: Callable[[], Dict[str, Any]],
        working_directory: Path,
        bg_store: Optional[BackgroundTaskStore] = None,
        event_sink: Optional[Callable[[AgentEvent], None]] = None,
        confirm_tool: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        ask_user: Optional[Callable[..., str]] = None,
        max_context_tokens: Optional[int] = None,
        parent_messages_getter: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        cancellation_token_getter: Optional[Callable] = None,
        readonly_mode_getter: Callable[[], bool] = lambda: False,
        sandbox_policy: Optional[Any] = None,
        subagent_emitter: Optional[Any] = None,
        subagent_relay: Optional[Any] = None,
        filesystem: Optional[Any] = None,
        shell: Optional[Any] = None,
        permission_engine_getter: Optional[Callable] = None,
        tool_origin_getter: Optional[Callable[[str], str]] = None,
        skill_manager_getter: Optional[Callable[[], Any]] = None,
        usage_sink: Optional[Callable[..., None]] = None,
        bg_owner: Any = None,
    ):
        self._definition = definition
        # The parent's live registry: tools it adds or removes between turns
        # count for sub-agents launched afterwards (see ``_narrow_tools``).
        self._all_tools = all_tools
        # The origin the parent's registry recorded for each of those names.
        # Absent, every parent tool reads as a host tool and is left out.
        self._tool_origin_getter = tool_origin_getter or (lambda _name: "host")
        # Live getter so a runtime ``session/set_model`` (model /
        # maxTokens) is reflected in sub-agents launched afterwards.
        self._llm_config_getter = llm_config_getter
        # Sub-agent runs in the same working directory as its parent. Frozen at
        # construction; matches the static-value idiom used for sandbox_policy.
        self._working_directory = working_directory
        self._bg_store = bg_store
        # The parent agent's key on ``bg_store``: its ``close()`` cancels the
        # background tasks launched under it, and only those.
        self._bg_owner = bg_owner
        # The parent's side of a foreground run: where its events go, who
        # answers its confirmations and questions. Each reads the parent's
        # transport per call, since replay swaps that transport after the
        # agent tools are registered. ``None``: nothing shown, and no one
        # asked (``SdkTransport``'s defaults).
        self._event_sink = event_sink
        self._confirm_tool = confirm_tool
        self._ask_user = ask_user
        self._max_context_tokens = max_context_tokens
        self._parent_messages_getter = parent_messages_getter
        self._cancellation_token_getter = cancellation_token_getter
        self._readonly_mode_getter = readonly_mode_getter
        # The parent's permission engine, read at spawn: a sub-agent decides
        # with a snapshot of it (see ``_drive_sub_agent``).
        self._permission_engine_getter = permission_engine_getter
        # The parent's skill manager, read at spawn: a sub-agent is built with
        # a child view of it (``_child_skill_manager``). Live, because the
        # CLI registers plugin skills onto it after construction.
        self._skill_manager_getter = skill_manager_getter
        # Where a finished sub-agent's token usage goes: the parent's session
        # totals. A sub-agent has its own ``LLMClient``, so without this its
        # requests are in nobody's total (``_roll_up_usage``).
        self._usage_sink = usage_sink
        self._sandbox_policy = sandbox_policy
        # Where the parent's file and shell tools run (a host may redirect them
        # into a container or a virtual filesystem). A sub-agent's built-ins
        # are its own instances, bound to these same backends; ``None`` means
        # the local machine, for the parent and the sub-agent alike.
        self._filesystem = filesystem
        self._shell = shell
        # Public host emitter for sub-agent lineage events. ``None``
        # for hosts that haven't wired the emitter; the call sites
        # short-circuit when the emitter is missing.
        self._subagent_emitter = subagent_emitter
        # Republishes a run's host events on the parent's stream when the
        # parent turned it on (``SubagentRelay``); ``None`` relays nothing.
        self._subagent_relay = subagent_relay
        # Set by ToolRunner just before execute() to propagate the per-turn token
        self._cancellation_token: Optional[Any] = None
        # Set by the executor under this tool's lock, just before execute():
        # the id of the ``agent_*`` call being run (#580).
        self._parent_tool_call_id: Optional[str] = None

    @property
    def name(self) -> str:
        return f"agent_{self._definition['name'].replace('-', '_')}"

    @property
    def description(self) -> str:
        return self._definition["description"]

    @property
    def spawns_shell_capable_agent(self) -> bool:
        """Whether the sub-agent this spawns can run ``run_shell_command``.

        Read by the MCP Skills gate (docs/design/mcp-skills.md §6.2): with an
        MCP skill loaded, spawning such a sub-agent is asked, since it is the
        one way around the gate on the shell itself. Mirrors
        ``_narrow_tools``: no ``tools:`` list means every parent tool.
        """
        if SHELL_TOOL_NAME not in self._all_tools:
            return False
        requested = self._definition.get("tools")
        return requested is None or SHELL_TOOL_NAME in requested

    @property
    def parameters(self) -> Dict[str, Any]:
        # Hide ``run_in_background`` from the LLM when the bg subsystem is disabled.
        properties: Dict[str, Any] = {
            "task": {
                "type": "string",
                "description": "Task description to delegate to this agent",
            },
        }
        if self._bg_store is not None:
            # Read at request time: a host may change the store's cap
            # after this tool was built.
            limit = getattr(self._bg_store, "max_concurrent", None)
            cap_note = (
                f"\n\nAt most {limit} background agents run at the same time. "
                "The tool refuses a launch above that limit."
                if type(limit) is int else ""
            )
            properties["run_in_background"] = {
                "type": "boolean",
                "description": (
                    "If true, run the agent asynchronously. The tool returns an "
                    "agent_id at once.\n"
                    "- Continue with other work. Do not wait with sleep or with "
                    "repeated status checks.\n"
                    "- If you have no other work, end this turn. You can read the "
                    "update of a background agent the next time this session runs.\n"
                    "- If this turn cannot continue without the result, call "
                    "check_background_agent with wait_seconds one time."
                    + cap_note
                ),
            }
        return {
            "type": "object",
            "properties": properties,
            "required": ["task"],
        }

    # ------------------------------------------------------------------
    # Public execute — dispatches sync vs background
    # ------------------------------------------------------------------

    def execute(self, task: str, run_in_background: bool = False) -> str:
        # The child's skill manager is derived here, on the launching thread
        # and *before* the parent's messages are read: deriving it registers
        # the conversation generation it reads (MCP Skills), so a ``/clear``
        # or a resume that lands before a background worker starts cannot
        # hand the old conversation's context to a child gated by the new,
        # empty generation. In the other order the race fails safe — new
        # context, old gates.
        skills = _child_skill_manager(self._skill_manager_getter, self._definition["name"])
        parent_context = self._build_parent_context()

        # Resolve the current cancellation token: prefer the one injected by
        # ToolRunner (set just before this call), fall back to the getter.
        token = self._cancellation_token or (
            self._cancellation_token_getter() if self._cancellation_token_getter else None
        )
        # Reset per-call injected token so it doesn't linger across calls.
        self._cancellation_token = None
        # The same for the call id: read once, so a later call made without
        # the executor never reports this one's id.
        parent_tool_call_id = self._parent_tool_call_id
        self._parent_tool_call_id = None

        if run_in_background:
            if self._bg_store is None:
                # Schema-level removal of ``run_in_background`` in
                # ``parameters`` keeps this unreachable for well-behaved
                # LLMs. Reaching it means a replay / hand-crafted call
                # is asking for a disabled feature — surface it loudly
                # rather than silently rewriting to sync execution.
                raise ValueError(
                    "run_in_background=True but background-agent store "
                    "is disabled (bg_store=None) on this runtime."
                )
            return self._launch_background(
                task, parent_context, skill_manager=skills,
                parent_tool_call_id=parent_tool_call_id,
            )

        agent_name = self._definition["name"]
        max_turns  = self._definition.get("max_turns", 15)

        # One id for this run, on every transport event it sends and on its
        # public ``SubagentLifecycleEvent`` (#578). Minted here, not by the
        # emitter: ``AGENT_START`` goes out before ``spawned`` is published,
        # and a run without an emitter needs the id too. A local, never an
        # attribute, so two runs of this tool cannot read each other's.
        from ...runtime.identity import new_child_task_id, new_session_id
        child_task_id = new_child_task_id()
        # The child's session, minted before ``spawned`` so the event can
        # point at it; the child adopts it once built.
        child_session_id = new_session_id()

        # Signal sub-agent start to the CLI. Not guarded, unlike the end
        # event, and on purpose: a sink that raises here fails the tool call
        # before anything has run, as it always has. Guarding it would let
        # the run go ahead without a start, a behaviour change of its own
        # (``docs/design/subagent-event-bridge.md`` §5 guards only the end).
        if self._event_sink:
            self._event_sink(AgentEvent(EventType.AGENT_START, {
                "agent": agent_name, "task": task[:80], "max_turns": max_turns,
                "child_task_id": child_task_id,
            }))

        # ``task`` is user-supplied free-form text (and may carry the
        # parent's tool output, secrets, or sensitive instructions);
        # ``redact_summary`` in the projection layer only flattens
        # whitespace, so the public ``SubagentLifecycleEvent`` would
        # otherwise leak the first 80 chars of that input verbatim.
        # Use a generic non-sensitive label — hosts have ``agent_name``
        # via the parent ``tool_name`` and can correlate via the
        # ``child_task_id`` already on the envelope.
        task_summary = f"sub-agent: {agent_name}"
        subagent_ctx = self._spawn_subagent_event(
            task_summary, child_task_id=child_task_id,
            parent_tool_call_id=parent_tool_call_id,
            child_session_id=child_session_id,
        )

        # Filled by ``_run_sync``'s ``finally``, so it is there on the two
        # raising paths below as well as on the ordinary one.
        settled: Dict[str, Any] = {}
        try:
            result, stats = self._run_sync(
                task, parent_context, cancellation_token=token, settled=settled,
                skill_manager=skills, child_task_id=child_task_id,
                child_session_id=child_session_id,
            )
        except AgentCancelledError:
            # Defensive only: ``runtime/turn.py`` maps this to
            # ``status="cancelled"`` and returns, so ``chat()`` does not raise
            # it and an ordinary cancel is classified below (#244). Kept for a
            # cancellation raised outside the turn — building or closing the
            # sub-agent — where there is no classification to read.
            #
            # Neither raising path has ``stats``. ``settled["counts"]`` has
            # what the sub-agent's history and clock showed when it raised;
            # without it — the sub-agent was never run — every counter is
            # "not known" and left out of the event, so the displays do not
            # show it as zero. A cancel leaves ``error`` empty, as on the
            # ordinary path below.
            self._send_agent_end(
                "cancelled", child_task_id=child_task_id,
                **settled.get("counts", {}),
            )
            self._terminal_subagent_event(
                subagent_ctx, "cancelled", task_summary, usage=settled.get("usage"),
            )
            raise
        except Exception as exc:
            self._send_agent_end(
                "failed", error=type(exc).__name__,
                child_task_id=child_task_id,
                **settled.get("counts", {}),
            )
            self._terminal_subagent_event(
                subagent_ctx, "failed", task_summary, error_type=type(exc).__name__,
                usage=settled.get("usage"),
            )
            raise

        incomplete = stats.get("incomplete")
        state = _terminal_state(incomplete)

        # Signal sub-agent end to the CLI
        self._send_agent_end(
            state,
            turns=stats["turns"],
            tool_calls=stats["tool_calls"],
            tokens=stats["tokens"],
            duration_ms=stats["duration_ms"],
            # ``error`` is the failure detail, so a cancel leaves it
            # empty: ``state`` carries that fact and the display
            # labels it. Filling it in would put "it was cancelled"
            # where the CLI renders a crash reason.
            error=incomplete.detail if state == "failed" else None,
            child_task_id=child_task_id,
        )

        if state == "failed":
            # The run did not raise, but it did not answer either. Reporting
            # ``completed`` here would make the public host contract state
            # something untrue; the phase vocabulary already has the value
            # this deserves.
            self._terminal_subagent_event(
                subagent_ctx,
                "failed",
                task_summary,
                error_type=f"incomplete:{incomplete.reason}",
                usage=settled.get("usage"),
            )
        else:
            # ``completed`` or ``cancelled`` — neither carries an error type.
            self._terminal_subagent_event(
                subagent_ctx, state, task_summary, usage=settled.get("usage"),
            )
        return self._format_result(result, stats)

    # ------------------------------------------------------------------
    # Public-event helpers
    # ------------------------------------------------------------------

    def _send_agent_end(
        self,
        state: str,
        *,
        error: Optional[str] = None,
        turns: Optional[int] = None,
        tool_calls: Optional[int] = None,
        tokens: Optional[int] = None,
        duration_ms: Optional[int] = None,
        child_task_id: Optional[str] = None,
    ) -> None:
        """Send ``AGENT_END`` without letting the send mask anything.

        Every foreground run that sent ``AGENT_START`` sends this exactly
        once, on a return and on an ``Exception`` alike, and always before the
        public terminal event. The CLI and ACP transports swallow their own
        errors, but an embedding host's may not: a raise here would replace
        the run's own exception, or skip the public terminal event.

        A counter (``turns``, ``tool_calls``, ``tokens``,
        ``duration_ms``) that is ``None`` is not known and is left out of
        the event, not sent as ``None``: the CLI and ACP leave a missing
        counter out of their lines, the compat ``step_callback`` reads it as
        0, and replay records ``None``.
        """
        if not self._event_sink:
            return
        data: Dict[str, Any] = {"agent": self._definition["name"], "state": state}
        counts = {
            "turns": turns, "tool_calls": tool_calls,
            "tokens": tokens, "duration_ms": duration_ms,
        }
        data.update((k, v) for k, v in counts.items() if v is not None)
        data["error"] = error
        if child_task_id is not None:
            data["child_task_id"] = child_task_id
        try:
            self._event_sink(AgentEvent(EventType.AGENT_END, data))
        except Exception:
            logger.warning(
                "sub-agent %s: sending the end-of-run event raised",
                self._definition["name"], exc_info=True,
            )

    def _attach_relay(self, sub_agent: Any, child_session_id: Optional[str]) -> Any:
        """Start relaying the run's host events, when the parent asked for it."""
        if self._subagent_relay is None:
            return None
        return self._subagent_relay.attach(sub_agent, child_session_id)

    def _detach_relay(self, handle: Any) -> None:
        if self._subagent_relay is not None:
            self._subagent_relay.detach(handle)

    def _spawn_subagent_event(
        self,
        task_summary: str,
        *,
        parent_task_id: Optional[str] = None,
        child_task_id: Optional[str] = None,
        parent_tool_call_id: Optional[str] = None,
        child_session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self._subagent_emitter is None:
            return None
        # ``None`` (the background path) lets the emitter mint the id.
        try:
            return self._subagent_emitter.spawned(
                task_summary=task_summary, parent_task_id=parent_task_id,
                child_task_id=child_task_id,
                parent_tool_call_id=parent_tool_call_id,
                child_session_id=child_session_id,
            )
        except Exception:
            return None

    def _terminal_subagent_event(
        self,
        ctx: Optional[Dict[str, Any]],
        phase: str,
        task_summary: str,
        *,
        error_type: Optional[str] = None,
        usage: Optional[Dict[str, int]] = None,
    ) -> None:
        if self._subagent_emitter is None or ctx is None:
            return
        try:
            if phase == "completed":
                self._subagent_emitter.completed(
                    ctx=ctx, task_summary=task_summary, usage=usage,
                )
            elif phase == "cancelled":
                self._subagent_emitter.cancelled(
                    ctx=ctx, task_summary=task_summary, usage=usage,
                )
            else:  # "failed"
                self._subagent_emitter.failed(
                    ctx=ctx, task_summary=task_summary, error_type=error_type,
                    usage=usage,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Parent context injection
    # ------------------------------------------------------------------

    def _build_parent_context(self) -> str:
        """Summarise the last N parent messages as a context block."""
        if not self._parent_messages_getter:
            return ""
        try:
            msgs = self._parent_messages_getter()
        except Exception:
            return ""
        if not msgs:
            return ""

        from ...skills.provenance import (
            is_skill_state_message,
            result_marker,
            skill_origins,
            strip_markers,
        )

        # A parent skill's SKILL.md the runtime persisted (#552) is not the
        # user's words, and cut at 400 chars it would leave an unclosed
        # reminder in the child's prompt. Dropped before the window is taken,
        # so it does not displace a real message from the excerpt.
        recent = [m for m in msgs if not is_skill_state_message(m)]
        recent = recent[-self.PARENT_CONTEXT_MESSAGES:]
        lines: List[str] = []
        for m in recent:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            # The excerpt lands in the child's ``user`` message, where any
            # origin marker is trusted — but there it has lost the tool name
            # that said whether to trust it. So every marker is removed, and
            # one is re-added for the origins trusted on the whole original
            # message (a quoted phrase in ``read_file`` output has none).
            origins = skill_origins([m]) if isinstance(m, dict) else set()
            mark = f"\n{result_marker(origins)}" if origins else ""
            # ``""`` for an empty or ``None`` content: the tool branch below
            # slices it, and ``None[:300]`` would raise out of the spawn.
            content = strip_markers(str(content)) if content else ""
            if role == "tool":
                name = m.get("name", "tool")
                lines.append(
                    f"[tool/{_indent_continuation(str(name))}]: "
                    f"{_indent_continuation(content[:300])}{mark}"
                )
            elif role in ("user", "assistant") and (content or mark):
                lines.append(f"[{role}]: {_indent_continuation((content or '')[:400])}{mark}")
            elif role == "assistant" and m.get("tool_calls"):
                tc_names = []
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict):
                        tc_names.append(tc.get("function", {}).get("name", "?"))
                    else:
                        tc_names.append(getattr(getattr(tc, "function", None), "name", "?"))
                lines.append(
                    f"[assistant called: {_indent_continuation(', '.join(map(str, tc_names)))}]"
                )

        if not lines:
            return ""
        return "[Parent conversation context (last {} messages)]\n{}\n".format(
            len(recent), "\n".join(lines)
        )

    # ------------------------------------------------------------------
    # Synchronous execution core
    # ------------------------------------------------------------------

    def _run_sync(
        self, task: str, parent_context: str = "", suppress_output: bool = False,
        cancellation_token: Optional[Any] = None,
        settled: Optional[Dict[str, Any]] = None,
        skill_manager: Optional[Any] = None,
        child_task_id: Optional[str] = None,
        child_session_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Create, run and close a sub-agent. Returns (result, stats).

        ``settled``, when given, receives ``"usage"`` from the ``finally`` —
        an out-parameter because the caller needs it on the raising paths too,
        where there is no return value to carry it. For the same reason, a
        run that raised after the sub-agent started leaves ``"counts"``: the
        counters ``stats`` would have held, read at the raise.

        The foreground path. A background run composes the same three steps
        in a different order — see ``_launch_background``.

        Args:
            suppress_output: When True, the child's events are not
                forwarded to the parent, so a background thread does not
                interleave output with the foreground session.
        """
        sub_agent, setup = self._build_sub_agent(
            suppress_output, skill_manager, child_task_id=child_task_id,
            child_session_id=child_session_id,
        )
        # Everything after construction runs under ``finally``: the sub-agent
        # holds resources of its own (its memory stores), and nothing else
        # releases them, since it is a local the parent's ``close()`` does not
        # reach. The ``try`` opens here rather than around ``chat()`` because
        # the setup in ``_drive_sub_agent`` can raise too.
        relay = None
        try:
            relay = self._attach_relay(sub_agent, child_session_id)
            return self._drive_sub_agent(
                sub_agent,
                task=task,
                parent_context=parent_context,
                cancellation_token=cancellation_token,
                started=settled,
                **setup,
            )
        except Exception:
            if settled is not None and "started" in settled:
                settled["counts"] = self._history_counts(
                    sub_agent, settled["started"],
                )
            raise
        finally:
            # First: once the drive has ended no child thread publishes, and
            # nothing may be relayed after the terminal event ``execute()``
            # publishes (design §5.4.3).
            self._detach_relay(relay)
            usage = self._roll_up_usage(sub_agent)
            if settled is not None:
                settled["usage"] = usage
            self._close_sub_agent(sub_agent)

    def _build_sub_agent(
        self, suppress_output: bool, skill_manager: Optional[Any] = None,
        *, child_task_id: Optional[str] = None,
        child_session_id: Optional[str] = None,
    ) -> Tuple[Any, Dict[str, Any]]:
        """Construct a sub-agent. Returns it with the keyword arguments
        ``_drive_sub_agent`` needs from the same config read.

        ``skill_manager`` is the child view :meth:`execute` derived at the
        call; ``None`` derives one now.

        The caller owns the returned sub-agent and must pass it to
        ``_close_sub_agent``.
        """
        from ...agent import Agentao
        from ...mcp.registry import InMemoryMCPRegistry

        defn_model: Optional[str] = self._definition.get("model")
        defn_temperature: Optional[float] = self._definition.get("temperature")

        # Inherit the parent's *current* LLM config — a mid-run
        # ``session/set_model`` must reach sub-agents launched afterwards.
        live_cfg = self._llm_config_getter()
        if defn_model and "/" in defn_model:
            _, model_name = defn_model.split("/", 1)
        else:
            model_name = defn_model or live_cfg.get("model")
        api_key = live_cfg["api_key"]
        base_url = live_cfg.get("base_url")

        temperature = (
            defn_temperature if defn_temperature is not None
            else live_cfg.get("temperature")
        )
        # Inherit the parent's temperature-omission state, but only when the
        # sub-agent isn't pinning its own temperature (an explicit definition
        # temperature means "send this value").
        omit_temperature = (
            False if defn_temperature is not None
            else bool(live_cfg.get("omit_temperature", False))
        )
        max_tokens = live_cfg.get("max_tokens")
        # Inherit the parent's request-body passthrough (extra_body) so a
        # host-set reasoning_effort / provider-mandatory field reaches
        # sub-agent LLM calls too — None when unset.
        extra_body = live_cfg.get("extra_body")
        # Same endpoint, so the same prompt-cache posture (stage 0b).
        prompt_cache = live_cfg.get("prompt_cache")
        prompt_cache_ttl = live_cfg.get("prompt_cache_ttl")
        # And the same wire protocol — the endpoint speaks one.
        api_format = live_cfg.get("api_format")

        max_turns = self._definition.get("max_turns", 15)
        agent_name = self._definition["name"]
        transport = self._child_transport(
            suppress_output, max_turns, child_task_id=child_task_id,
        )

        # Built here, so a constructor that raises does not leak it: to
        # ``Agentao`` it is injected, and an injected manager is the caller's.
        child_memory = _child_memory_manager(agent_name)
        try:
            sub_agent = Agentao(
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                prompt_cache=prompt_cache,
                prompt_cache_ttl=prompt_cache_ttl,
                api_format=api_format,
                working_directory=self._working_directory,
                sandbox_policy=self._sandbox_policy,
                filesystem=self._filesystem,
                shell=self._shell,
                # The parent's logger. Left out, the sub-agent's ``LLMClient``
                # evicts and closes the ``agentao.log`` handler the parent
                # installed on the package logger and installs its own — from a
                # background sub-agent's thread, while the parent is still logging
                # through it. ``None`` for a parent that has no logger yet.
                logger=live_cfg.get("logger"),
                # No MCP source of its own: a sub-agent calls the parent's MCP
                # tools over the parent's connections (``_narrow_tools``). Left to
                # the default it read ``mcp.json`` again and launched every server
                # a second time for each spawn (#239), and it missed the servers a
                # host had passed to the parent in code.
                mcp_registry=InMemoryMCPRegistry(),
                # The parent's skills, with activation state of its own (#254) —
                # and said at construction rather than assigned after it:
                # ``activate_skill`` is built from whatever manager the agent
                # holds, so replacing the attribute afterwards left the tool
                # activating skills out of a manager the system prompt is no
                # longer built from.
                skill_manager=(
                    skill_manager if skill_manager is not None
                    else _child_skill_manager(self._skill_manager_getter, agent_name)
                ),
                # A memory store of the child's own: transient, unread, and
                # discarded with it (#234). Left to the default it opened the
                # parent's project ``memory.db``, and its compaction wrote both a
                # session summary and crystallized proposals into it — see
                # ``_child_memory_manager`` for why the read side goes with them.
                memory_manager=child_memory,
                # The parent's background-task store, so the sub-agent's own
                # ``check_background_agent`` / ``cancel_background_agent`` query
                # and cancel the same tasks the parent's do.
                bg_store=self._bg_store,
                transport=transport,
                max_context_tokens=self._max_context_tokens or 200_000,
            )
        except BaseException:
            # Best effort, so the constructor's exception is the one that
            # propagates.
            try:
                child_memory.close()
            except Exception:
                logger.warning(
                    "memory close after a failed sub-agent construction",
                    exc_info=True,
                )
            raise
        # The sub-agent's own from here: its ``close()`` releases it.
        sub_agent._adopt_memory_manager(child_memory)
        # The id ``spawned`` already points at, before the child publishes
        # anything.
        if child_session_id is not None:
            # The caller's ``finally`` opens only once this returns, so a
            # raise here would leak the child it never received.
            try:
                sub_agent._adopt_session_id(child_session_id)
            except BaseException:
                self._close_sub_agent(sub_agent)
                raise

        return sub_agent, {
            "omit_temperature": omit_temperature,
            "max_turns": max_turns,
        }

    def _narrow_tools(self, sub_agent: Any) -> None:
        """Give the sub-agent one registry, for its model and its runner (#238).

        The runner and its planner hold the registry the sub-agent was built
        with, so its contents are replaced in place. Assigning a new registry to
        ``sub_agent.tools`` changed only what the model was shown: calls still
        resolved against everything construction had registered, so
        ``complete_task`` was never found, a tool outside the definition's
        ``tools:`` list still ran, and so did the sub-agent's own agent tools.

        The contents are the parent's tools at spawn time, narrowed to the
        definition's ``tools:`` list (all of them when it has none). So a tool
        the parent disabled, pruned or removed is not a candidate at all. Of
        the rest:

        - **a built-in**: the sub-agent's own instance, bound to its own
          transport and todo list. ``save_memory`` is the exception — its write
          target is rebound to the parent's ``MemoryManager``, because a
          long-term memory has to land where the parent's memories land, user
          store and host injection included (#260). The rest of the child's
          memory stays its own: its session id, the session summaries and
          crystallized proposals its own compaction writes, and the transient
          store its ``close()`` releases — which is also why it reads no
          memories at all (#234);
        - **an MCP tool**: the parent's instance, which calls through the
          parent's connection, so the sub-agent opens none;
        - **an agent tool**: left out, so a sub-agent cannot spawn another;
        - **a plan-only tool**: left out;
        - **a host tool** reaches the sub-agent only when the tool object
          declares ``copies_to_subagents``, and then as one ``copy.copy`` made
          here, registered with origin ``host`` (SUB-03 / PR-b). A tool that
          declares nothing is left out **by name**: falling back to the
          built-in under that name would run the very implementation the host
          replaced. Sharing the instance is what the copy exists to avoid —
          the executor rebinds ``output_callback`` on it per call under a lock
          scoped to one batch, and a sub-agent's batch holds a different one.

        Which of these a parent tool is comes from the origin the parent's
        registry recorded when it was registered (``ToolRegistry.origin``),
        never from its class: a host that replaced ``web_search`` with a
        configured ``WebSearchTool`` registered a host tool (#256). A built-in
        is kept only when the sub-agent's tool under that name is a built-in
        too. ``complete_task`` is always added.
        """
        own = sub_agent.tools
        requested = self._definition.get("tools")
        agent_name = self._definition["name"]
        kept: List[Tuple[RegistrableTool, str]] = []
        left_out: List[str] = []
        # A resource link in an MCP tool's result names ``read_mcp_resource``
        # only when the agent running the tool can call it. The parent's
        # instances check the parent's registry, so a child without the tool
        # gets copies with the hint off.
        child_reads = _READ_RESOURCE_TOOL in self._all_tools and (
            requested is None or _READ_RESOURCE_TOOL in requested
        )
        for name, tool in list(self._all_tools.items()):
            if requested is not None and name not in requested:
                continue
            try:
                origin = self._tool_origin_getter(name)
            except KeyError:  # removed from the parent since the snapshot
                continue
            if origin == "mcp":
                if not child_reads:
                    tool = _without_read_hint(tool)
                # The two skill-reading tools read one conversation generation
                # of the shared MCP skills: the sub-agent's own, not the
                # parent's current one (docs/design/mcp-skills.md, Appendix B).
                for_view = getattr(type(tool), "for_view", None)
                if callable(for_view):
                    tool = tool.for_view(
                        getattr(getattr(sub_agent, "skill_manager", None), "mcp_view", None)
                    )
                kept.append((tool, "mcp"))
            elif origin == "builtin" and name in own.tools and own.origin(name) == "builtin":
                own_tool = own.tools[name]
                if name == _PARENT_MEMORY_TARGET_TOOL and not _bind_parent_memory_target(
                    own_tool, tool, agent_name,
                ):
                    left_out.append(name)
                else:
                    kept.append((own_tool, "builtin"))
            elif origin == "host":
                copied = _copy_declared_host_tool(tool, name, agent_name)
                if copied is None:
                    left_out.append(name)
                else:
                    kept.append((copied, "host"))
            else:
                left_out.append(name)
        complete = CompleteTaskTool()
        kept.append((complete, "builtin"))
        for name in list(own.tools):
            own.unregister(name)
        for tool, origin in kept:
            own.register(tool, origin=origin)

        if requested is None:
            if left_out:
                logger.debug(
                    "Sub-agent '%s' does not get the parent's %s",
                    agent_name, ", ".join(sorted(left_out)),
                )
            return
        if left_out:
            logger.warning(
                "Sub-agent '%s' lists tools it does not get: %s. A sub-agent gets "
                "built-in and MCP tools, plus host tools that declare "
                "`copies_to_subagents`; agent and plan tools are left out, and so "
                "is '%s' when its write target cannot be rebound to the parent's "
                "memory manager (a separate warning above says which).",
                agent_name, ", ".join(sorted(left_out)),
                _PARENT_MEMORY_TARGET_TOOL,
            )
        unavailable = sorted(set(requested) - set(self._all_tools) - {complete.name})
        if unavailable:
            logger.debug(
                "Sub-agent '%s' lists tools the parent does not have: %s",
                agent_name, ", ".join(unavailable),
            )

    @staticmethod
    def _count_history(sub_agent: Any) -> Tuple[int, int, int]:
        """``(turns, tool_calls, tokens)`` from the sub-agent's history.

        ``tokens`` is the estimated size of that history, not what its
        requests were billed: that is ``_roll_up_usage``.
        """
        messages = sub_agent.messages
        turns = sum(1 for m in messages if m.get("role") == "assistant")
        tool_calls = sum(1 for m in messages if m.get("role") == "tool")
        return turns, tool_calls, sub_agent.context_manager.estimate_tokens(messages)

    def _history_counts(
        self, sub_agent: Any, started: float,
    ) -> Dict[str, int]:
        """The end event's counters for a run that raised.

        Best effort, so the run's own exception is the one that propagates:
        when the history cannot be read, its counters are left out ("not
        known") and only the measured duration is kept.
        """
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            turns, tool_calls, tokens = self._count_history(sub_agent)
        except Exception:
            logger.warning(
                "Reading a sub-agent's history after it raised failed (%s)",
                self._definition["name"], exc_info=True,
            )
            return {"duration_ms": duration_ms}
        return {
            "turns": turns, "tool_calls": tool_calls,
            "tokens": tokens, "duration_ms": duration_ms,
        }

    def _roll_up_usage(self, sub_agent: Any) -> Optional[Dict[str, int]]:
        """Add what this sub-agent's requests cost to the parent's totals,
        and return it for the record and the terminal event.

        **Once per sub-agent, and before its outcome is published** — a host
        told "completed" reads the parent's totals from its handler, and the
        background path used to publish first and add afterwards. A run that
        raised or was cancelled is counted too: its requests were made and
        paid for whatever came of them. One level is the whole tree, since
        agent tools are withheld from a sub-agent (``_narrow_tools``).

        Same footing as ``_close_sub_agent``: a failure here is logged and
        never replaces the outcome. The counts are type-checked **here**, not
        only where they land: the dict becomes a ``SubagentUsage``, and a
        value that model refused would take the terminal event down with it.
        ``None`` means the counts could not be read, not that they were zero.
        """
        try:
            llm = sub_agent.llm
            # One locked read where the client offers it; a cancelled run's
            # stream consumer may still be adding. A host's own ``llm_client``
            # need not, so what came back is checked, not that it answered.
            snapshot = getattr(llm, "usage_snapshot", None)
            counts = snapshot() if callable(snapshot) else None
            if not isinstance(counts, dict):
                counts = {
                    "prompt_tokens": llm.total_prompt_tokens,
                    "completion_tokens": llm.total_completion_tokens,
                    "cache_read_tokens": getattr(llm, "total_cache_read_tokens", 0),
                    "cache_creation_tokens": getattr(llm, "total_cache_creation_tokens", 0),
                    "reasoning_tokens": getattr(llm, "total_reasoning_tokens", 0),
                }
            # A key a host's snapshot lacks is read from its attribute.
            usage = usage_totals(counts, llm)
        except Exception:
            logger.warning(
                "Reading a sub-agent's token usage failed (%s)",
                self._definition["name"], exc_info=True,
            )
            return None
        if self._usage_sink is not None:
            # ``reasoning_tokens`` only when there are some and the sink takes
            # the keyword: a sink written before it would raise ``TypeError``
            # and lose the whole roll-up, prompt and completion counts included.
            try:
                # Inside the ``try``: a sink's ``__signature__`` is host code.
                extra = (
                    {"reasoning_tokens": usage["reasoning_tokens"]}
                    if usage["reasoning_tokens"]
                    and accepts_keyword(self._usage_sink, "reasoning_tokens")
                    else {}
                )
                self._usage_sink(
                    usage["prompt_tokens"], usage["completion_tokens"],
                    cache_read_tokens=usage["cache_read_tokens"],
                    cache_creation_tokens=usage["cache_creation_tokens"],
                    **extra,
                )
            except Exception:
                logger.warning(
                    "Rolling a sub-agent's token usage up to its parent failed (%s)",
                    self._definition["name"], exc_info=True,
                )
        return usage

    def _close_sub_agent(self, sub_agent: Any) -> None:
        """Release a sub-agent's resources.

        By the time this runs the run's outcome is decided, and on the
        background path already published, so a failure here is logged and
        never replaces that outcome.
        """
        try:
            sub_agent.close()
        except Exception:
            logger.warning(
                "Closing a sub-agent failed (%s)", self._definition["name"],
                exc_info=True,
            )

    def _drive_sub_agent(
        self,
        sub_agent: Any,
        *,
        task: str,
        parent_context: str,
        omit_temperature: bool,
        max_turns: int,
        cancellation_token: Optional[Any],
        started: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Configure a constructed sub-agent, run it, and collect its stats.

        The caller owns the sub-agent's lifetime and closes it whatever this
        raises; stats read the sub-agent's history, so they are taken here,
        before that close. ``started``, when given, receives ``"started"``,
        the clock reading the run's duration is measured from, so a caller
        whose run raised can still time it.
        """
        sub_agent.llm.omit_temperature = omit_temperature
        self._narrow_tools(sub_agent)
        # The store is shared (``_build_sub_agent``) for querying and
        # cancelling, not for consuming: a sub-agent's loop draining it would
        # take notifications addressed to the top-level conversation into its
        # own history. Every runtime this wrapper builds is a non-consumer, so
        # a task launched at any depth reports to the top level; a sub-agent
        # whose tool list includes ``check_background_agent`` can poll for its
        # result.
        sub_agent._drains_background_notifications = False
        sub_agent.project_instructions = self._definition.get("system_instructions")
        # ``skill_manager`` is set at construction (``_build_sub_agent``), not
        # here: ``activate_skill`` binds to whatever manager the agent held
        # when it was built, so assigning the attribute afterwards left that
        # tool operating on a manager the system prompt no longer reads.
        # ``_narrow_tools`` already left the agent tools out; without this the
        # system prompt would still list agents the sub-agent cannot call.
        sub_agent.agent_manager = None
        if self._readonly_mode_getter():
            sub_agent.tool_runner.set_readonly_mode(True)
        parent_engine = (
            self._permission_engine_getter()
            if self._permission_engine_getter is not None
            else None
        )
        if parent_engine is not None:
            # A snapshot of the parent's policy, not a re-read of the files.
            # Rules can live only on the engine (a host's ``rules=``, an
            # ``agentao run`` spec), and a re-read missed them, so the mode
            # preset allowed what the parent denied. Set on the agent and
            # through the runner's setter: the planner holds the engine it
            # decides with, and assigning the runner's attribute alone left it
            # deciding with none.
            engine = parent_engine.snapshot(project_root=sub_agent.working_directory)
            sub_agent.permission_engine = engine
            sub_agent.tool_runner.set_permission_engine(engine)

        # Prepend parent context to the task
        # ``task`` is the model's text and lands in the child's ``user``
        # message, where a whole-line marker is trusted: one it contains is
        # quoted, not provenance (the real origins are in the context).
        from ...skills.provenance import strip_markers

        task = strip_markers(task) if isinstance(task, str) else task
        if parent_context:
            full_task = f"{parent_context}\n[Your Task]\n{task}"
        else:
            full_task = task

        # ``max_iterations`` exhaustion is a deliberately separate axis from
        # ``incomplete_reason`` (see runtime/chat_loop/_runner.py:85) and rides
        # a transport flag that only ``NonInteractiveTransport`` carries — the
        # sub-agent has no such transport. Since sub-agents are handed a
        # *smaller* budget than the parent, cap exhaustion is their most likely
        # way to stop short, so record it here rather than let it read as
        # success. The prior handler's decision is preserved verbatim.
        max_iter_hit = {"hit": False}
        _prior_on_max_iter = getattr(sub_agent.transport, "on_max_iterations", None)

        def _note_max_iterations(max_iterations, pending):
            max_iter_hit["hit"] = True
            if callable(_prior_on_max_iter):
                return _prior_on_max_iter(max_iterations, pending)
            return {"action": "stop"}

        try:
            sub_agent.transport.on_max_iterations = _note_max_iterations
        except Exception:
            # Read-only / slotted transport: lose the signal rather than the run.
            pass

        t0 = time.monotonic()
        if started is not None:
            started["started"] = t0
        task_complete = False
        try:
            # Foreground sub-agents share the parent's cancellation token so
            # Ctrl+C propagates into nested chat() loops (Gemini CLI pattern).
            # Background agents receive their own task token, which
            # ``BackgroundTaskStore.cancel`` signals.
            result = sub_agent.chat(
                full_task,
                max_iterations=max_turns,
                cancellation_token=cancellation_token,
            )
        except TaskComplete as tc:
            # Defensive only: ``ToolExecutor`` converts ``TaskComplete`` into
            # a tool result, so it does not reach here from the normal path.
            # The real detection is the history scan below.
            result = tc.result
            task_complete = True

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # An explicit completion signal from the sub-agent: it called
        # ``complete_task``. That is the agent declaring it is done, so the
        # turn-level classification does not apply — do not second-guess it.
        completed_payload = _find_task_complete_result(sub_agent)
        if completed_payload is not None:
            task_complete = True
            # ``complete_task`` is documented as *the* way a sub-agent
            # returns its answer, but the loop keeps running after the tool
            # call and the child often has nothing left to say — leaving
            # ``chat()`` to return the empty-turn placeholder. Prefer the
            # payload the agent explicitly handed back over that placeholder,
            # otherwise the real answer is dropped on the floor.
            if _is_harness_notice(result) and completed_payload.strip():
                result = completed_payload

        # Collect stats from executed sub-agent
        turns, tool_calls, approx_tokens = self._count_history(sub_agent)

        from ...skills.provenance import skill_origins

        stats = {
            "agent_name": self._definition["name"],
            # MCP skill content the child's transcript held: the parent's
            # copy of its result must carry that provenance (restore,
            # compaction), whatever the child chose to repeat.
            "mcp_origins": sorted(
                skill_origins(sub_agent.messages) | _session_mcp_origins(sub_agent)
            ),
            "turns": turns,
            "tool_calls": tool_calls,
            "tokens": approx_tokens,
            "duration_ms": elapsed_ms,
            "incomplete": _classify_subagent_outcome(
                outcome=getattr(sub_agent, "last_turn", None),
                task_complete=task_complete,
                max_iterations_hit=max_iter_hit["hit"],
                max_turns=max_turns,
            ),
        }
        return result, stats

    @staticmethod
    def _format_result(result: str, stats: Dict[str, Any]) -> str:
        """:meth:`_format_body`, led by the MCP origin marker when there is one.

        First, not in the footer: a background task's notification is a short
        preview of the head of its result, and any truncation keeps the head.
        The marker has to survive both.
        """
        from ...skills.provenance import result_marker, strip_markers

        # The child's answer is model text: a marker in it is quoted, and
        # would be trusted on this tool's result. Its real origins are
        # ``stats["mcp_origins"]``.
        if isinstance(result, str):
            result = strip_markers(result)
        body = AgentToolWrapper._format_body(result, stats)
        if not stats.get("mcp_origins"):
            return body

        return result_marker(stats["mcp_origins"]) + "\n" + body

    @staticmethod
    def _format_body(result: str, stats: Dict[str, Any]) -> str:
        """Render the sub-agent's result for the parent LLM.

        A stats footer on its own reads as an affirmative productivity
        signal, so when the sub-agent stopped short it has to be said
        plainly *before* the text — otherwise ``[No response]`` plus
        "8 turns, 12 tool calls" invites the parent to treat a non-answer
        as a finished piece of work.
        """
        name = stats["agent_name"]
        footer = (
            f"[{name}: {stats['turns']} turns, {stats['tool_calls']} tool calls, "
            f"~{stats['tokens']:,} tokens, {stats['duration_ms']}ms]"
        )

        note = stats.get("incomplete")
        if not note:
            return f"{result}\n\n{footer}"

        header = f"[{name} did not finish: {note.detail}]"
        # Only genuine sub-agent output earns the "Partial result" label —
        # ``[No response]``, ``[LLM API error: …]`` and the max-iterations
        # notice are all harness-authored, and presenting them as the
        # child's work is the misattribution this guard exists to prevent.
        if not _is_harness_notice(result):
            return f"{header}\nPartial result:\n{result}\n\n{footer}"
        return f"{header}\n\n{footer}"

    # ------------------------------------------------------------------
    # Background (async) execution
    # ------------------------------------------------------------------

    def _launch_background(
        self, task: str, parent_context: str, skill_manager: Optional[Any] = None,
        *, parent_tool_call_id: Optional[str] = None,
    ) -> str:
        agent_id = uuid.uuid4().hex[:8]
        agent_name = self._definition["name"]
        # First, so a launch refused for capacity (``BackgroundCapacityError``)
        # leaves nothing behind: no record, no token, no ``spawned`` event
        # without a terminal one. The executor reports the raise as a failed
        # tool call whose text tells the model what to do instead.
        self._bg_store.register(agent_id, agent_name, task[:80], owner=self._bg_owner)

        token = CancellationToken()
        self._bg_store.register_token(agent_id, token)

        # Generic non-sensitive label — see the foreground spawn site
        # for the redaction-boundary rationale. ``task`` is intentionally
        # NOT included on the public event.
        task_summary = f"sub-agent: {agent_name}"
        from ...runtime.identity import new_session_id
        child_session_id = new_session_id()
        subagent_ctx = self._spawn_subagent_event(
            task_summary, parent_task_id=agent_id,
            parent_tool_call_id=parent_tool_call_id,
            child_session_id=child_session_id,
        )

        def _run():
            if not self._bg_store.mark_running(agent_id):
                # Pending-cancel race: the agent was cancelled before
                # the worker started running. ``spawned`` already fired
                # outside this thread, so without an explicit terminal
                # event host subscribers would see a child task that
                # never completes. Emit ``cancelled`` so the lifecycle
                # pair is closed.
                self._terminal_subagent_event(subagent_ctx, "cancelled", task_summary)
                return
            # The same build / drive / close as ``_run_sync``, but the close
            # comes last, after the outcome is published. A close that took
            # time (it once disconnected the sub-agent's own MCP servers, which
            # could take seconds) kept the record ``running`` for that whole
            # window: ``check_background_agent`` had no result yet, and a
            # cancel sent then was acknowledged for a run that had already
            # finished.
            #
            # Usage is settled *before* the outcome is published, not in the
            # ``finally`` with the close: ``update`` queues the completion
            # notice and the terminal event reaches host subscribers, and
            # either reader then looks at the parent's totals. Settled after,
            # those totals were still missing this run when they looked.
            sub_agent = None
            settled: Dict[str, Any] = {}

            def _settle() -> Optional[Dict[str, int]]:
                # Once: three paths publish, and the ``finally`` is the fourth
                # caller, for a drive that raised ``BaseException``.
                if "usage" not in settled:
                    settled["usage"] = (
                        self._roll_up_usage(sub_agent) if sub_agent is not None else None
                    )
                return settled["usage"]

            try:
                sub_agent, setup = self._build_sub_agent(
                    suppress_output=True, skill_manager=skill_manager,
                    child_session_id=child_session_id,
                )
                relay = self._attach_relay(sub_agent, child_session_id)
                # Detached as the drive ends, before any terminal event below
                # is published (design §5.4.3).
                try:
                    result, stats = self._drive_sub_agent(
                        sub_agent,
                        task=task,
                        parent_context=parent_context,
                        cancellation_token=token,
                        **setup,
                    )
                finally:
                    self._detach_relay(relay)
                formatted = self._format_result(result, stats)
                # Not raising is not the same as finishing. The result text
                # is still stored either way — a partial answer is useful —
                # but the status and the public event must say what actually
                # happened, or ``check_background_agent`` and any host
                # subscriber both read a non-answer as a success.
                incomplete = stats.get("incomplete")
                state = _terminal_state(incomplete)
                usage = _settle()
                self._bg_store.update(
                    agent_id,
                    status=state,
                    usage=usage,
                    # Passed on *every* terminal state, cancelled included:
                    # ``update`` overwrites the record's result and its four
                    # counters unconditionally, so a cancel that omitted them
                    # would erase the work the run did before it was stopped.
                    result=formatted,
                    error=incomplete.detail if state == "failed" else None,
                    # The record must say *which* kind of "failed" this is.
                    # Without it every reader has to guess from the presence
                    # of `result`, and the CLI guessed wrong — it printed the
                    # error and discarded the work. Set only alongside
                    # ``failed``, per the field's contract in ``bg_store``: on
                    # a cancel the status already names the cause.
                    incomplete_reason=incomplete.reason if state == "failed" else None,
                    turns=stats["turns"], tool_calls=stats["tool_calls"],
                    tokens=stats["tokens"], duration_ms=stats["duration_ms"],
                )
                if state == "failed":
                    self._terminal_subagent_event(
                        subagent_ctx, "failed", task_summary,
                        error_type=f"incomplete:{incomplete.reason}", usage=usage,
                    )
                else:
                    self._terminal_subagent_event(
                        subagent_ctx, state, task_summary, usage=usage,
                    )
            except AgentCancelledError:
                # Defensive only — see the foreground site. ``chat()`` does not
                # raise this, so an ordinary cancel is classified above and
                # keeps its partial result and counters. Reaching here means
                # the cancellation came from outside the drive, where there is
                # no run to record.
                usage = _settle()
                self._bg_store.update(agent_id, status="cancelled", usage=usage)
                self._terminal_subagent_event(
                    subagent_ctx, "cancelled", task_summary, usage=usage,
                )
            except Exception as exc:
                # A drive that raised still made its requests.
                usage = _settle()
                self._bg_store.update(
                    agent_id, status="failed", error=str(exc), usage=usage,
                )
                self._terminal_subagent_event(
                    subagent_ctx, "failed", task_summary,
                    error_type=type(exc).__name__, usage=usage,
                )
            finally:
                self._bg_store.unregister_token(agent_id)
                if sub_agent is not None:
                    _settle()
                    self._close_sub_agent(sub_agent)

        # Background agents run silently: suppress_output=True ensures no events
        # reach the parent from the background thread, preventing interleaving
        # with foreground output.
        t = threading.Thread(target=_run, daemon=True, name=f"bg-agent-{agent_id}")
        t.start()

        return (
            f"Background agent '{agent_name}' started (ID: {agent_id}). "
            f"Task: {task[:80]}{'…' if len(task) > 80 else ''}. "
            "Continue other work; do not wait with sleep or repeated status checks. "
            "If there is nothing else to do, end this turn. "
            "A background agent update can be read when this session next runs. "
            "If this turn cannot continue without its result, call "
            f"check_background_agent(agent_id='{agent_id}', wait_seconds=N) once."
        )

    def _child_transport(
        self, suppress_output: bool, max_turns: int,
        *, child_task_id: Optional[str] = None,
    ) -> SdkTransport:
        """The transport a sub-agent is built with.

        A foreground sub-agent reports to the parent through
        :meth:`_make_forwarder` and asks through it: ``confirm`` prepends
        "[agent_name]" to the tool name so the user knows which one is asking.
        Unset, the child's ``SdkTransport`` answers exactly as a
        ``NullTransport`` does (approve, the non-interactive ``ask_user``
        sentinel, stop at max iterations).

        A background sub-agent has nobody to ask, and must not read stdin from
        its thread (that corrupts the terminal's raw mode). So its transport
        refuses every call that needs confirmation: a transport that answered
        as ``NullTransport`` does would approve them all. It can still run
        whatever permission rules allow outright, and a denial still denies.
        ``NullTransport`` itself keeps approving, since that is the documented
        default for a host with no callbacks. Nothing it emits reaches the
        parent.
        """
        if suppress_output:
            return SdkTransport(confirm_tool=lambda *_: False)
        agent_name = self._definition["name"]
        confirm = None
        if self._confirm_tool:
            _parent_confirm = self._confirm_tool

            def confirm(tool_name: str, tool_desc: str, tool_args: dict) -> bool:
                return _parent_confirm(f"[{agent_name}] {tool_name}", tool_desc, tool_args)

        return SdkTransport(
            on_event=self._make_forwarder(max_turns, child_task_id=child_task_id),
            confirm_tool=confirm,
            ask_user=self._ask_user,
        )

    # ------------------------------------------------------------------
    # The foreground child's events, forwarded to the parent
    # ------------------------------------------------------------------

    def _make_forwarder(
        self, max_turns: int, *, child_task_id: Optional[str] = None,
    ) -> Optional[Callable[[AgentEvent], None]]:
        """The ``on_event`` of a foreground child's transport, or ``None``.

        A whitelist: only the events the parent shows as the child's tool
        rows reach it. ``THINKING`` and ``LLM_TEXT`` stay with the child (its
        reasoning is not shown), and so do ``TURN_BEGIN`` / ``TURN_END`` /
        ``LLM_CALL_*`` / ``TOOL_RESULT``, which replay would record as the
        parent's own turns.

        The defaults below are the ones the parent applied before this was
        a forwarder (``docs/design/subagent-event-bridge.md`` §6.3). Every
        event sent is a new one: the child's transport hands the same object
        to its own subscribers, so it is never changed in place.

        ``child_task_id`` is the run's id (#578). :func:`send` sets it on
        every forwarded event, over any value the child's event had, so a
        host can tell a run's events from the parent's and from another
        run's. ``None`` sends the events without it.
        """
        sink = self._event_sink
        if sink is None:
            return None
        agent_name = self._definition["name"]
        # One counter for both labels: the ACP ``tool_call`` a confirmation
        # opens carries this label until the ``TOOL_START`` update restates
        # it, so the two should name the call identically from the start.
        turn = [0]

        def label(tool: Any) -> str:
            # ``cli/display.py::_SUBAGENT_LABEL_RE`` splits this format back
            # into label and tool name; change the two together.
            return f"[{agent_name} {turn[0]}/{max_turns}] {tool}"

        def send(event: AgentEvent) -> None:
            # Every event reaching here has a ``data`` dict built in
            # ``forward``, so setting the key changes nothing the child holds.
            if child_task_id is not None:
                event.data["child_task_id"] = child_task_id
            sink(event)

        def forward(event: AgentEvent) -> None:
            t, d = event.type, event.data
            if t == EventType.TURN_START:
                # Before each LLM iteration, and after a rejected
                # confirmation, which only resets the display and is not a
                # turn.
                reset = bool(d.get("display_reset"))
                if not reset:
                    turn[0] += 1
                send(AgentEvent(EventType.TURN_START, {"display_reset": True} if reset else {}))
            elif t == EventType.TOOL_START:
                tool = label(d.get("tool", ""))
                args = d.get("args", {})
                send(AgentEvent(EventType.TOOL_START, {
                    "tool": tool,
                    "args": dict(args) if isinstance(args, dict) else {},
                    # A missing or empty id falls back to the label.
                    "call_id": d.get("call_id") or tool,
                }))
            elif t == EventType.TOOL_CONFIRMATION:
                # ACP opens the call's ``tool_call`` from this event, before
                # the permission request, so the attribution has to be here or
                # the client shows an unattributed call while the user is
                # being asked about it.
                data = dict(d)
                data["tool"] = label(d.get("tool", ""))
                send(replace(event, data=data))
            elif t == EventType.TOOL_OUTPUT:
                tool, call_id = d.get("tool", ""), d.get("call_id")
                send(AgentEvent(EventType.TOOL_OUTPUT, {
                    "tool": tool, "chunk": d.get("chunk", ""),
                    # Only a missing id falls back to the name; an empty one
                    # is kept, so it is not silently collapsed.
                    "call_id": call_id if call_id is not None else tool,
                }))
            elif t == EventType.TOOL_COMPLETE:
                tool, call_id = d.get("tool", ""), d.get("call_id")
                status, duration_ms = d.get("status"), d.get("duration_ms")
                send(AgentEvent(EventType.TOOL_COMPLETE, {
                    "tool": tool,
                    "call_id": call_id if call_id is not None else tool,
                    # The real outcome, so a failed or denied tool is not
                    # reported as a success.
                    "status": status if status is not None else "ok",
                    "duration_ms": duration_ms if duration_ms is not None else 0,
                    "error": d.get("error"),
                }))

        return forward
