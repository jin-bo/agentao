"""``AgentToolWrapper`` — wraps an agent definition as a callable Tool.

The largest piece of the sub-agent system: turns a YAML-style agent
definition (name + description + system prompt + scoped tools + max
turns) into something the parent LLM can invoke via OpenAI function
calling. The wrapper's ``execute`` decides sync vs background dispatch,
builds the parent-context block, scopes the sub-agent's ToolRegistry,
spawns a fresh :class:`Agentao` for the sub-task, and emits public
``SubagentLifecycleEvent`` pairs so hosts can observe lineage.

The class stays in one file because its sub-helpers (parent-context
builder, sync runner, background launcher, prefixed step callback) all read
the same constructor-captured callbacks. The free functions that read none
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
from ...llm._usage import positive_int
from ...tools.base import SHELL_TOOL_NAME, RegistrableTool, Tool
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
from ._progress import SubagentProgress

logger = logging.getLogger(__name__)


_USAGE_KEYS = (
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens",
)


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
        confirmation_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
        output_callback: Optional[Callable] = None,
        tool_complete_callback: Optional[Callable] = None,
        ask_user_callback: Optional[Callable] = None,
        max_context_tokens: Optional[int] = None,
        parent_messages_getter: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        cancellation_token_getter: Optional[Callable] = None,
        readonly_mode_getter: Callable[[], bool] = lambda: False,
        sandbox_policy: Optional[Any] = None,
        subagent_emitter: Optional[Any] = None,
        filesystem: Optional[Any] = None,
        shell: Optional[Any] = None,
        permission_engine_getter: Optional[Callable] = None,
        tool_origin_getter: Optional[Callable[[str], str]] = None,
        skill_manager_getter: Optional[Callable[[], Any]] = None,
        usage_sink: Optional[Callable[..., None]] = None,
        confirmation_event_callback: Optional[Callable] = None,
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
        self._confirmation_callback = confirmation_callback
        self._confirmation_event_callback = confirmation_event_callback
        self._step_callback = step_callback
        self._output_callback = output_callback
        self._tool_complete_callback = tool_complete_callback
        self._ask_user_callback = ask_user_callback
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
        # Set by ToolRunner just before execute() to propagate the per-turn token
        self._cancellation_token: Optional[Any] = None

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

    # Sentinel tool names used to signal sub-agent lifecycle to the step callback
    _AGENT_START = "__agent_start__"
    _AGENT_END   = "__agent_end__"

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
            return self._launch_background(task, parent_context, skill_manager=skills)

        agent_name = self._definition["name"]
        max_turns  = self._definition.get("max_turns", 15)

        # Signal sub-agent start to the CLI
        if self._step_callback:
            self._step_callback(
                self._AGENT_START,
                SubagentProgress(agent_name=agent_name, state="running",
                                 task=task[:80], max_turns=max_turns),
            )

        # ``task`` is user-supplied free-form text (and may carry the
        # parent's tool output, secrets, or sensitive instructions);
        # ``redact_summary`` in the projection layer only flattens
        # whitespace, so the public ``SubagentLifecycleEvent`` would
        # otherwise leak the first 80 chars of that input verbatim.
        # Use a generic non-sensitive label — hosts have ``agent_name``
        # via the parent ``tool_name`` and can correlate via the
        # ``child_task_id`` already on the envelope.
        task_summary = f"sub-agent: {agent_name}"
        subagent_ctx = self._spawn_subagent_event(task_summary)

        # Filled by ``_run_sync``'s ``finally``, so it is there on the two
        # raising paths below as well as on the ordinary one.
        settled: Dict[str, Any] = {}
        try:
            result, stats = self._run_sync(
                task, parent_context, cancellation_token=token, settled=settled,
                skill_manager=skills,
            )
        except AgentCancelledError:
            # Defensive only: ``runtime/turn.py`` maps this to
            # ``status="cancelled"`` and returns, so ``chat()`` does not raise
            # it and an ordinary cancel is classified below (#244). Kept for a
            # cancellation raised outside the turn — building or closing the
            # sub-agent — where there is no classification to read.
            self._terminal_subagent_event(
                subagent_ctx, "cancelled", task_summary, usage=settled.get("usage"),
            )
            raise
        except Exception as exc:
            self._terminal_subagent_event(
                subagent_ctx, "failed", task_summary, error_type=type(exc).__name__,
                usage=settled.get("usage"),
            )
            raise

        incomplete = stats.get("incomplete")
        state = _terminal_state(incomplete)

        # Signal sub-agent end to the CLI
        if self._step_callback:
            self._step_callback(
                self._AGENT_END,
                SubagentProgress(
                    agent_name=agent_name,
                    state=state,
                    task=task[:80],
                    max_turns=max_turns,
                    turns=stats["turns"],
                    tool_calls=stats["tool_calls"],
                    tokens=stats["tokens"],
                    duration_ms=stats["duration_ms"],
                    # ``error`` is the failure detail, so a cancel leaves it
                    # empty: ``state`` carries that fact and the display
                    # labels it. Filling it in would put "it was cancelled"
                    # where the CLI renders a crash reason.
                    error=incomplete.detail if state == "failed" else None,
                ),
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

    def _spawn_subagent_event(
        self,
        task_summary: str,
        *,
        parent_task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self._subagent_emitter is None:
            return None
        try:
            return self._subagent_emitter.spawned(
                task_summary=task_summary, parent_task_id=parent_task_id,
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

        from ...skills.provenance import result_marker, skill_origins, strip_markers

        recent = msgs[-self.PARENT_CONTEXT_MESSAGES:]
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
    ) -> Tuple[str, Dict[str, Any]]:
        """Create, run and close a sub-agent. Returns (result, stats).

        ``settled``, when given, receives ``"usage"`` from the ``finally`` —
        an out-parameter because the caller needs it on the raising paths too,
        where there is no return value to carry it.

        The foreground path. A background run composes the same three steps
        in a different order — see ``_launch_background``.

        Args:
            suppress_output: When True, all live display callbacks are
                suppressed so a background thread does not interleave output
                with the foreground session.
        """
        sub_agent, setup = self._build_sub_agent(suppress_output, skill_manager)
        # Everything after construction runs under ``finally``: the sub-agent
        # holds resources of its own (its memory stores), and nothing else
        # releases them, since it is a local the parent's ``close()`` does not
        # reach. The ``try`` opens here rather than around ``chat()`` because
        # the setup in ``_drive_sub_agent`` can raise too.
        try:
            return self._drive_sub_agent(
                sub_agent,
                task=task,
                parent_context=parent_context,
                cancellation_token=cancellation_token,
                **setup,
            )
        finally:
            usage = self._roll_up_usage(sub_agent)
            if settled is not None:
                settled["usage"] = usage
            self._close_sub_agent(sub_agent)

    def _build_sub_agent(
        self, suppress_output: bool, skill_manager: Optional[Any] = None,
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
        # One counter for both labels: the ACP ``tool_call`` a confirmation
        # opens carries this label until the ``TOOL_START`` update restates
        # it, so the two should name the call identically from the start.
        turn_counter = [0]
        step_cb = (
            None if suppress_output
            else self._make_prefixed_step_callback(max_turns, turn_counter)
        )

        # A foreground sub-agent asks through the parent: the callback prepends
        # "[agent_name]" to the tool name so the user knows which one is asking.
        # Its callbacks ride one compat transport — with none of them set that
        # transport answers exactly as a ``NullTransport`` does (approve, the
        # non-interactive ``ask_user`` sentinel, stop at max iterations).
        #
        # A background sub-agent has nobody to ask, and must not read stdin from
        # its thread (that corrupts the terminal's raw mode). So it refuses every
        # call that needs confirmation, through a transport of its own: with no
        # callbacks at all it would get a ``NullTransport``, which approves them
        # all. It can still run whatever permission rules allow outright, and a
        # denial still denies. ``NullTransport`` itself keeps approving, since
        # that is the documented default for a host with no callbacks.
        if suppress_output:
            from ...transport import SdkTransport

            transport = SdkTransport(confirm_tool=lambda *_: False)
        else:
            from ...transport import build_compat_transport

            confirm_cb = None
            if self._confirmation_callback:
                _parent_cb = self._confirmation_callback
                def confirm_cb(tool_name: str, tool_desc: str, tool_args: dict) -> bool:
                    return _parent_cb(f"[{agent_name}] {tool_name}", tool_desc, tool_args)

            # No ``thinking_callback``: a sub-agent's reasoning is not shown.
            transport = build_compat_transport(
                confirmation_callback=confirm_cb,
                confirmation_event_callback=self._make_prefixed_confirmation_event_callback(
                    max_turns, turn_counter
                ),
                step_callback=step_cb,
                output_callback=self._output_callback,
                tool_complete_callback=self._tool_complete_callback,
                ask_user_callback=self._ask_user_callback,
            )

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
            memory_manager=_child_memory_manager(agent_name),
            # The parent's background-task store, so the sub-agent's own
            # ``check_background_agent`` / ``cancel_background_agent`` query
            # and cancel the same tasks the parent's do.
            bg_store=self._bg_store,
            transport=transport,
            max_context_tokens=self._max_context_tokens or 200_000,
        )

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
                }
            usage = {key: positive_int(counts.get(key)) for key in _USAGE_KEYS}
        except Exception:
            logger.warning(
                "Reading a sub-agent's token usage failed (%s)",
                self._definition["name"], exc_info=True,
            )
            return None
        if self._usage_sink is not None:
            try:
                self._usage_sink(
                    usage["prompt_tokens"], usage["completion_tokens"],
                    cache_read_tokens=usage["cache_read_tokens"],
                    cache_creation_tokens=usage["cache_creation_tokens"],
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
    ) -> Tuple[str, Dict[str, Any]]:
        """Configure a constructed sub-agent, run it, and collect its stats.

        The caller owns the sub-agent's lifetime and closes it whatever this
        raises; stats read the sub-agent's history, so they are taken here,
        before that close.
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
        turns = sum(1 for m in sub_agent.messages if m.get("role") == "assistant")
        tool_calls = sum(1 for m in sub_agent.messages if m.get("role") == "tool")
        approx_tokens = sub_agent.context_manager.estimate_tokens(sub_agent.messages)

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
    ) -> str:
        agent_id = uuid.uuid4().hex[:8]
        agent_name = self._definition["name"]
        # First, so a launch refused for capacity (``BackgroundCapacityError``)
        # leaves nothing behind: no record, no token, no ``spawned`` event
        # without a terminal one. The executor reports the raise as a failed
        # tool call whose text tells the model what to do instead.
        self._bg_store.register(agent_id, agent_name, task[:80])

        token = CancellationToken()
        self._bg_store.register_token(agent_id, token)

        # Generic non-sensitive label — see the foreground spawn site
        # for the redaction-boundary rationale. ``task`` is intentionally
        # NOT included on the public event.
        task_summary = f"sub-agent: {agent_name}"
        subagent_ctx = self._spawn_subagent_event(task_summary, parent_task_id=agent_id)

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
                )
                result, stats = self._drive_sub_agent(
                    sub_agent,
                    task=task,
                    parent_context=parent_context,
                    cancellation_token=token,
                    **setup,
                )
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

        # Background agents run silently: suppress_output=True ensures no callbacks
        # fire on the background thread, preventing interleaving with foreground output.
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

    # ------------------------------------------------------------------
    # Progress callback with turn counter
    # ------------------------------------------------------------------

    def _make_prefixed_step_callback(
        self, max_turns: int, turn_counter: Optional[List[int]] = None
    ) -> Optional[Callable]:
        parent_cb = self._step_callback
        if not parent_cb:
            return None
        agent_name = self._definition["name"]
        if turn_counter is None:
            turn_counter = [0]  # mutable cell

        def prefixed(tool_name: Optional[str], tool_args: dict) -> None:
            if tool_name is None:
                # Called before each LLM iteration — increment turn counter —
                # and after a rejected confirmation, which only resets the
                # display and is not a turn.
                if not (tool_args or {}).get("display_reset"):
                    turn_counter[0] += 1
                parent_cb(None, tool_args)  # keep the "Thinking…" reset
            else:
                label = f"[{agent_name} {turn_counter[0]}/{max_turns}] {tool_name}"
                parent_cb(label, tool_args)

        return prefixed

    def _make_prefixed_confirmation_event_callback(
        self, max_turns: int, turn_counter: List[int]
    ) -> Optional[Callable]:
        """Forward a sub-agent's ``TOOL_CONFIRMATION`` under the step label.

        ACP opens the call's ``tool_call`` from this event, before the
        permission request, so the attribution has to be here or the client
        shows an unattributed call while the user is being asked about it.
        """
        parent_cb = self._confirmation_event_callback
        if not parent_cb:
            return None
        agent_name = self._definition["name"]

        def prefixed(event: Any) -> None:
            data = dict(event.data)
            label = f"[{agent_name} {turn_counter[0]}/{max_turns}] {data.get('tool', '')}"
            data["tool"] = label
            parent_cb(replace(event, data=data))

        return prefixed
