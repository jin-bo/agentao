"""``AgentToolWrapper`` — wraps an agent definition as a callable Tool.

The largest piece of the sub-agent system: turns a YAML-style agent
definition (name + description + system prompt + scoped tools + max
turns) into something the parent LLM can invoke via OpenAI function
calling. The wrapper's ``execute`` decides sync vs background dispatch,
builds the parent-context block, scopes the sub-agent's ToolRegistry,
spawns a fresh :class:`Agentao` for the sub-task, and emits public
``SubagentLifecycleEvent`` pairs so hosts can observe lineage.

Kept as a single file because the public surface is one class — the
sub-helpers (parent-context builder, sync runner, background launcher,
prefixed step callback) all read closures over the same constructor-
captured callbacks and would just leak that state across files if
extracted.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...cancellation import AgentCancelledError, CancellationToken
from ...tools.base import RegistrableTool, Tool
from ..bg_store import BackgroundTaskStore, BgTaskStatus
from ._complete import CompleteTaskTool, TaskComplete
from ._progress import SubagentProgress

logger = logging.getLogger(__name__)


# Human-readable renderings of the ``TurnOutcome.incomplete_reason`` closed
# vocabulary. The parent LLM reads these, so they say what happened in plain
# terms rather than echoing the wire token.
_INCOMPLETE_DETAILS: Dict[str, str] = {
    "no_output": "it produced no output",
    "reasoning_only": "it produced only reasoning, with no answer",
    "length_truncated": "its answer was cut off by the model's output-length limit",
    "doom_loop": "it was halted after repeating the same tool call",
    "llm_error": "the LLM API call failed",
}

# Sentinel reason for budget exhaustion. Not part of the ``incomplete_reason``
# vocabulary — ``max_iterations`` is a separate axis by design — so it gets its
# own key rather than being smuggled into that closed set.
_MAX_ITERATIONS_REASON = "max_iterations"

# The one ``_IncompleteOutcome.reason`` that names a user action rather than a
# way of stopping short. Minted in exactly one place (the ``status ==
# "cancelled"`` branch of ``_classify_subagent_outcome``) and read in exactly
# one other (``_terminal_state``), so it is a constant rather than two string
# literals free to drift apart: it is the join key between what happened and
# what every surface calls it.
_CANCELLED_REASON = "cancelled"


# Handed to ``SkillManager`` to mean "no skills at all" — the documented
# spelling (``skills/manager.py``: "Pass a non-existent path to suppress all
# skills"), and what a sub-agent falls back to when the parent's catalogue
# cannot be derived.
_NO_SKILLS_DIR = "/nonexistent"


def _child_skill_manager(
    getter: Optional[Callable[[], Any]], agent_name: str,
) -> Any:
    """The sub-agent's ``SkillManager``: the parent's catalogue, its own
    activations (#254, via :meth:`SkillManager.child_view`).

    Read at spawn, not at registration: in the CLI a plugin's skills are
    registered onto the parent's manager *after* the agent is constructed,
    so a snapshot taken when this wrapper was built would miss them.

    Falls back to a manager with no skills — today's behaviour, and the
    fail-closed direction — when there is no getter, when the getter answers
    with nothing, when reading or deriving raises, when the object the host
    injected has no ``child_view``, and when ``child_view`` answers with
    nothing. A sub-agent with no skills is coherent: its ``activate_skill``
    is built from this same manager, so the enum is empty and an activation
    answers that the skill is unknown.

    Every one of those is an explicit ``_no_skills()``, never a ``None``
    handed on to the constructor: ``Agentao(skill_manager=None)`` means "scan
    for your own", which re-runs the three-directory discovery *and* the
    unlocked ``_bootstrap_bundled_skills`` ``copytree`` once per spawn, from
    background sub-agent threads — and hands the child a catalogue that is
    not the one the parent advertises. That is the whole failure this
    function exists to avoid, so it must not be reachable by falling open.
    """
    from ...skills import SkillManager

    def _no_skills() -> Any:
        return SkillManager(skills_dir=_NO_SKILLS_DIR)

    if getter is None:
        return _no_skills()
    try:
        parent = getter()
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' gets no skills: reading the parent's skill "
            "manager raised %s: %s",
            agent_name, type(exc).__name__, exc,
        )
        return _no_skills()
    if parent is None:
        # A getter was supplied and answered with nothing — the runtime has
        # no skill manager to derive from. Said out loud rather than assumed:
        # every other way to reach ``_no_skills()`` from here reports itself,
        # and a silently empty catalogue is indistinguishable from a sub-agent
        # whose host never wired skills up at all.
        logger.warning(
            "Sub-agent '%s' gets no skills: the parent has no skill manager.",
            agent_name,
        )
        return _no_skills()
    derive = getattr(parent, "child_view", None)
    if not callable(derive):
        logger.warning(
            "Sub-agent '%s' gets no skills: the parent's skill manager is a "
            "%s, which has no child_view().",
            agent_name, type(parent).__name__,
        )
        return _no_skills()
    try:
        child = derive()
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' gets no skills: deriving the parent's catalogue "
            "raised %s: %s",
            agent_name, type(exc).__name__, exc,
        )
        return _no_skills()
    if child is None:
        logger.warning(
            "Sub-agent '%s' gets no skills: %s.child_view() returned None. A "
            "sub-agent is never built with `skill_manager=None`, which would "
            "re-scan the skill directories per spawn and give it a catalogue "
            "the parent does not advertise.",
            agent_name, type(parent).__name__,
        )
        return _no_skills()
    return child


def _child_memory_manager(agent_name: str) -> Any:
    """The sub-agent's ``MemoryManager``: a store of its own that nothing reads (#234).

    Left to the default, a sub-agent's bare manager opens
    ``working_directory/.agentao/memory.db`` — the same file the CLI factory
    hands the *parent* as its project store. Two writers the child never sees
    follow from that, both from ``ContextManager.commit_compaction``: a session
    summary stamped with the child's own session id, which the parent's
    ``get_cross_session_tail()`` keeps *because* that id is not the parent's,
    and renders into ``<memory-stable>`` as an earlier session; and the
    crystallizer's proposals, which land in the parent's review queue, where
    ``/memory review approve`` can promote a sub-task's prompt into a memory
    the user never said. Neither needs ``/clear`` to happen, and no bulk
    remedy reaches the review queue: ``/memory review reject <id>`` retires
    one item at a time, and neither ``/clear`` nor ``/memory clear`` touches
    the table.

    A transient store closes both — and it is deliberately a whole store rather
    than a sink for those two writes: **a sub-agent reads no memories.** Its
    ``<memory-stable>`` block and its recall are empty, where until now they
    were the parent's project store, read through the shared file. That is the
    decision and not a side effect: a sub-agent is briefed by the
    ``parent_context`` it is spawned with — which carries the parent's recent
    *messages*, never its memories, so this is a narrowing and not a
    relocation — and gemini-cli's generalist arrives at the same place from
    the other direction (``userMemory=undefined``). Only
    the long-term *write* crosses over, through ``save_memory``'s rebound
    target (#260) — so the child can save a memory it cannot read back.
    Reversing the read side does not reopen this one: it wants a
    ``MemoryManager`` child view that shares the parent's stores and keeps a
    sink of its own, and whose ``close()`` must then not close what it shares.

    ``agent_name`` is for the log only. There is no fallback branch, and the
    difference from ``_child_skill_manager`` — which catches everything so an
    auxiliary subsystem can never fail a spawn — is deliberate: there the
    fallback (a catalogue with no skills) is coherent and fail-closed, and
    here the only candidate is ``None``, which means "open the project
    database" and reinstates the defect. So a store that cannot be built
    fails the spawn, which a transient sqlite3 store can only do where the
    parent's own store has already failed.
    """
    from ...memory import MemoryManager, SQLiteMemoryStore

    logger.debug(
        "Sub-agent '%s' gets a transient memory store of its own; it reads no "
        "memories and its compaction writes stay in it.", agent_name,
    )
    return MemoryManager(project_store=SQLiteMemoryStore(":memory:"))


def _copy_declared_host_tool(
    tool: RegistrableTool, name: str, agent_name: str,
) -> Optional[RegistrableTool]:
    """The copy a host tool contributes to a sub-agent, or ``None`` (SUB-03).

    ``None`` means the tool is absent from the sub-agent, and so is the name
    it occupied — the caller never falls back to sharing ``tool`` or to the
    built-in it may have replaced. Five ways to get there, and every failure
    logs once, naming the tool and what was wrong with it:

    - the tool does not declare ``copies_to_subagents`` (the default, and
      silent: an undeclared host tool being absent is the contract, not a
      fault);
    - reading the declaration raises. Fail closed: a property that cannot say
      yes has not said yes;
    - the declaration is **callable** — a host wrote the override as a plain
      method instead of a property. A bound method is truthy, so this is the
      one misreading of the mechanism that would otherwise fail *open*:
      ``def copies_to_subagents(self): return False`` would read as a yes;
    - ``copy.copy`` raises. A tool that declared it tolerates a copy and then
      cannot be copied is a host bug, and the exception is the only useful
      thing to report about it;
    - ``copy.copy`` answers with the original instance, or with a tool under
      another name. A ``__copy__`` returning ``self`` shares the very instance
      the copy exists to separate, and one returning a differently-named tool
      would be registered under that other name — where it can displace a
      built-in this sub-agent *is* keeping. Both are checked rather than
      trusted, because the promise the two carry ("never shared", "left out by
      name") is one the caller states unconditionally.

    The declaration is read here rather than at registration because a host
    may set it per instance, and because a property is free to answer from
    state that only exists once the tool is wired up.
    """
    try:
        declared = tool.copies_to_subagents
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' does not get host tool '%s': reading "
            "`copies_to_subagents` raised %s: %s",
            agent_name, name, type(exc).__name__, exc,
        )
        return None
    if callable(declared):
        logger.warning(
            "Sub-agent '%s' does not get host tool '%s': its "
            "`copies_to_subagents` is a %s rather than a bool — it looks like "
            "a method that is missing `@property`. A bound method is truthy "
            "whatever it returns, so it is read as no declaration.",
            agent_name, name, type(declared).__name__,
        )
        return None
    if not declared:
        return None
    try:
        copied = copy.copy(tool)
        copied_name = copied.name
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' does not get host tool '%s': it declares "
            "`copies_to_subagents` but copying it (or reading the copy's "
            "name) raised %s: %s. The parent's instance is never shared "
            "instead.",
            agent_name, name, type(exc).__name__, exc,
        )
        return None
    if copied is tool or copied_name != name:
        logger.warning(
            "Sub-agent '%s' does not get host tool '%s': copying it returned "
            "%s. The parent's instance is never shared, and a copy is never "
            "registered under a name other than the one it was taken from.",
            agent_name, name,
            "the original instance"
            if copied is tool else f"a tool named '{copied_name}'",
        )
        return None
    # The copy must not carry the parent's live ``output_callback``. At spawn
    # the parent may be inside a call on this same instance — a background
    # spawn takes its copy on the background thread while the parent's turn
    # carries on — and that closure emits ``TOOL_OUTPUT`` to the *parent's*
    # transport under the parent's call id. The sub-agent's executor rebinds
    # it per call; clearing it here makes the window before that first call
    # point at nothing, and drops the sub-agent's hold on the parent's
    # transport.
    # Written only when there is something to clear, so a tool that never had
    # the attribute does not acquire one (the executor keys its own binding on
    # ``hasattr``).
    try:
        if getattr(copied, "output_callback", None) is not None:
            copied.output_callback = None
    except Exception:  # a read-only or slotted attribute is not worth failing for
        logger.debug(
            "Could not clear `output_callback` on the sub-agent's copy of '%s'",
            name, exc_info=True,
        )
    return copied


# The one built-in whose write target belongs to the parent rather than to the
# sub-agent itself. ``save_memory`` writes *long-term* memory — a fact meant to
# outlive the conversation — so it has to land where the parent's memories
# land. A sub-agent's own manager is built on a transient store
# (``_child_memory_manager``, #234), so without the rebind the write would go
# where nothing reads it. Before #234 it was a bare project-scope manager on
# the parent's own ``memory.db`` file, which is why #260 is not stated as a
# black hole: in the CLI the row *was* readable, by accident of the shared
# path, and what was wrong is the rest — a ``scope="user"`` request was
# silently downgraded to project, and a host that injected a
# ``MemoryManager`` (never the store the child opened, either way) was not in
# the loop for anything any sub-agent saved (#260).
#
# Only the write target moves. This rebinds the *tool's* attribute, not
# ``sub_agent.memory_manager``: the agent property carries the session id, the
# session summaries the child's own compaction writes, and the stores its
# ``close()`` releases. Assigning it would mix the child's summaries into the
# parent's session and close the parent's stores the moment the child finished.
_PARENT_MEMORY_TARGET_TOOL = "save_memory"


def _bind_parent_memory_target(
    own_tool: RegistrableTool, parent_tool: RegistrableTool, agent_name: str,
) -> bool:
    """Point the sub-agent's ``save_memory`` at the parent's memory manager.

    True when the sub-agent's own instance now writes where the parent's
    writes. False leaves the tool **out** of the sub-agent, because the only
    alternative is the defect itself: a tool that reports "Saved memory: x"
    into a store nothing will read. Absent, the model is told the tool does not
    exist, which is at least true.

    Fail-closed on each of the three ways the rebind can fail — the parent's
    tool has no readable ``memory_manager``, that manager is ``None``, or the
    sub-agent's instance will not take the attribute. None of the three is
    reachable while
    both sides are the built-in :class:`~agentao.tools.SaveMemoryTool`, since a
    host that replaced ``save_memory`` registered a *host* tool and never
    reaches this branch — so each one means the assumption the rebind rests on
    has stopped holding, and guessing past it writes memories into the dark.
    """
    try:
        manager = parent_tool.memory_manager
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' does not get '%s': reading the parent tool's "
            "`memory_manager` raised %s: %s.",
            agent_name, _PARENT_MEMORY_TARGET_TOOL, type(exc).__name__, exc,
        )
        return False
    if manager is None:
        logger.warning(
            "Sub-agent '%s' does not get '%s': the parent's instance has no "
            "memory manager to write through.",
            agent_name, _PARENT_MEMORY_TARGET_TOOL,
        )
        return False
    try:
        # ``hasattr`` swallows only ``AttributeError``; a property that raises
        # anything else would otherwise propagate out of ``_narrow_tools`` and
        # abort the spawn, which is the one outcome this function exists to
        # avoid.
        keeps_one = hasattr(own_tool, "memory_manager")
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' does not get '%s': reading its own "
            "`memory_manager` raised %s: %s.",
            agent_name, _PARENT_MEMORY_TARGET_TOOL, type(exc).__name__, exc,
        )
        return False
    if not keeps_one:
        logger.warning(
            "Sub-agent '%s' does not get '%s': its own %s keeps no "
            "`memory_manager`, so the parent's cannot be bound to it.",
            agent_name, _PARENT_MEMORY_TARGET_TOOL, type(own_tool).__name__,
        )
        return False
    try:
        own_tool.memory_manager = manager
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' does not get '%s': binding the parent's memory "
            "manager to its own instance raised %s: %s.",
            agent_name, _PARENT_MEMORY_TARGET_TOOL, type(exc).__name__, exc,
        )
        return False
    return True


@dataclass(frozen=True)
class _IncompleteOutcome:
    """Why a sub-agent stopped short. ``reason`` is machine-readable."""

    reason: str
    detail: str


# Harness-authored turn text. None of these is sub-agent output, so none may
# be presented to the parent LLM as the child's "partial result" — doing so
# would attribute the harness's own notice to the sub-agent. The empty-turn
# placeholder is imported lazily (see ``_format_result``); the rest are the
# max-iterations and LLM-error notices from ``chat_loop/_runner.py`` and the
# two cancellation markers from ``runtime/turn.py``.
#
# The cancel markers are here because they are the *whole* text of a turn that
# ``AgentCancelledError`` or ``KeyboardInterrupt`` ended, so labelling them
# "Partial result" tells the parent LLM the child reported "[Cancelled:
# user-cancel]" as its work. A turn cancelled mid-stream returns whatever the
# model had produced instead, which is real output and stays labelled.
_HARNESS_NOTICE_PREFIXES = ("[LLM API error:", "[Cancelled:")
_HARNESS_NOTICE_EXACT = (
    "Maximum tool call iterations reached.",
    "[Interrupted by user]",
)


def _is_harness_notice(text: Optional[str]) -> bool:
    """True if ``text`` is agentao's own notice rather than model output.

    Used to decide what may be shown to the parent LLM as a sub-agent's
    "partial result". ``[No response]``, ``[LLM API error: …]``, "Maximum
    tool call iterations reached." and the two cancellation markers are all
    strings the harness authored on the child's behalf; labelling them as
    the child's partial work would attribute the harness's words to the
    sub-agent — the exact misreporting this whole path exists to stop.
    """
    # Deferred: ``agentao.runtime`` imports ``agentao.agents`` for
    # ``TaskComplete``, so a module-level import here is a cycle.
    from ...runtime.chat_loop._runner import EMPTY_RESPONSE_PLACEHOLDER

    body = (text or "").strip()
    if not body:
        return True
    if body == EMPTY_RESPONSE_PLACEHOLDER or body in _HARNESS_NOTICE_EXACT:
        return True
    return body.startswith(_HARNESS_NOTICE_PREFIXES)


def _find_task_complete_result(sub_agent: Any) -> Optional[str]:
    """Return the payload of the sub-agent's ``complete_task`` call, if any.

    ``CompleteTaskTool.execute`` raises ``TaskComplete``, but
    ``ToolExecutor._execute_one`` catches it and turns it into an ordinary
    tool result (``runtime/tool_executor.py:339``) — so it never propagates
    out of ``chat()`` and cannot be detected with ``except``. The durable
    signal is the tool result it leaves in the child's history.

    Returns the last such payload (``None`` if the tool was never called),
    which is both the "the agent declared itself done" flag and the answer
    it meant to hand back.
    """
    messages = getattr(sub_agent, "messages", None) or []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool" and msg.get("name") == "complete_task":
            content = msg.get("content")
            return content if isinstance(content, str) else ""
    return None


def _classify_subagent_outcome(
    *,
    outcome: Any,
    task_complete: bool,
    max_iterations_hit: bool,
    max_turns: int,
) -> Optional[_IncompleteOutcome]:
    """Decide whether a finished sub-agent run actually answered.

    Returns ``None`` when the run produced a real answer — the common
    case — and an :class:`_IncompleteOutcome` otherwise. Mirrors the
    top-level ``TurnOutcome`` contract (PR #126) one level down: a
    sub-agent that never answered must not be reported to the parent
    LLM, or to a host watching ``SubagentLifecycleEvent``, as a success.

    ``task_complete`` wins over everything: the sub-agent called
    ``complete_task``, which is an explicit "I am done" signal, and the
    turn-level classification of the call that carried it is irrelevant.
    """
    if task_complete:
        return None
    if max_iterations_hit:
        return _IncompleteOutcome(
            _MAX_ITERATIONS_REASON,
            f"it used its entire {max_turns}-turn budget without finishing",
        )
    if outcome is None:
        # No ``last_turn`` to read (a stubbed or older agent object).
        # Absence of evidence is not evidence of failure.
        return None
    if getattr(outcome, "is_answer", False):
        return None

    reason = getattr(outcome, "incomplete_reason", None)
    if reason:
        detail = _INCOMPLETE_DETAILS.get(reason, f"it stopped early ({reason})")
        return _IncompleteOutcome(reason, detail)

    status = getattr(outcome, "status", None)
    if status == "cancelled":
        return _IncompleteOutcome(_CANCELLED_REASON, "it was cancelled")
    if status == "error":
        return _IncompleteOutcome("error", "it ended with an error")
    # ``is_answer`` false with no reason and no bad status shouldn't happen;
    # report it honestly rather than papering over it as success.
    return _IncompleteOutcome("unknown", "it did not produce a complete answer")


def _terminal_state(incomplete: Optional[_IncompleteOutcome]) -> BgTaskStatus:
    """What to call a finished sub-agent run — the one mapping both paths use.

    Answers in the ``BgTaskStatus`` vocabulary that the background store, the
    foreground ``SubagentProgress`` and the public ``SubagentLifecycleEvent``
    phase all share, so a cancel cannot land in one terminal state on the
    foreground path and another on the background one.

    **Derived from the classification, never from whether an exception
    escaped.** Both paths used to decide this inline as ``"completed" if
    incomplete is None else "failed"`` and lean on an ``except
    AgentCancelledError`` to recover the cancelled case — a branch ``chat()``
    never reaches, so every running cancel was recorded as a failure (#244).
    A cancel arrives here three different ways and none of them is an
    exception by the time the wrapper sees it: ``AgentCancelledError`` and
    ``KeyboardInterrupt`` are both mapped to ``status="cancelled"`` in
    ``runtime/turn.py``, and a token cancelled mid-stream lets the turn return
    normally and is flipped to that same status in the ``finally`` there.

    ``complete_task`` and the turn budget keep the precedence
    :func:`_classify_subagent_outcome` gives them: a sub-agent that declared
    itself done and was cancelled a moment later reads ``completed``, and one
    that exhausted its budget as the cancel landed reads ``failed`` with
    ``max_iterations``. Both are the classifier's answer to what ended the
    run, and a cancel arriving after the fact does not rewrite it.
    """
    if incomplete is None:
        return "completed"
    if incomplete.reason == _CANCELLED_REASON:
        return "cancelled"
    return "failed"


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
    def parameters(self) -> Dict[str, Any]:
        # Hide ``run_in_background`` from the LLM when the bg subsystem is disabled.
        properties: Dict[str, Any] = {
            "task": {
                "type": "string",
                "description": "Task description to delegate to this agent",
            },
        }
        if self._bg_store is not None:
            properties["run_in_background"] = {
                "type": "boolean",
                "description": (
                    "Run the agent asynchronously (fire-and-forget). "
                    "Returns immediately with an agent_id. "
                    "Use check_background_agent to poll for the result. "
                    "Useful for long-running tasks that should not block."
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
            return self._launch_background(task, parent_context)

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

        try:
            result, stats = self._run_sync(
                task, parent_context, cancellation_token=token,
            )
        except AgentCancelledError:
            # Defensive only: ``runtime/turn.py`` maps this to
            # ``status="cancelled"`` and returns, so ``chat()`` does not raise
            # it and an ordinary cancel is classified below (#244). Kept for a
            # cancellation raised outside the turn — building or closing the
            # sub-agent — where there is no classification to read.
            self._terminal_subagent_event(subagent_ctx, "cancelled", task_summary)
            raise
        except Exception as exc:
            self._terminal_subagent_event(
                subagent_ctx, "failed", task_summary, error_type=type(exc).__name__,
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
            )
        else:
            # ``completed`` or ``cancelled`` — neither carries an error type.
            self._terminal_subagent_event(subagent_ctx, state, task_summary)
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
    ) -> None:
        if self._subagent_emitter is None or ctx is None:
            return
        try:
            if phase == "completed":
                self._subagent_emitter.completed(ctx=ctx, task_summary=task_summary)
            elif phase == "cancelled":
                self._subagent_emitter.cancelled(ctx=ctx, task_summary=task_summary)
            else:  # "failed"
                self._subagent_emitter.failed(
                    ctx=ctx, task_summary=task_summary, error_type=error_type,
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
            if role == "tool":
                name = m.get("name", "tool")
                lines.append(f"[tool/{name}]: {str(content)[:300]}")
            elif role in ("user", "assistant") and content:
                lines.append(f"[{role}]: {str(content)[:400]}")
            elif role == "assistant" and m.get("tool_calls"):
                tc_names = []
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict):
                        tc_names.append(tc.get("function", {}).get("name", "?"))
                    else:
                        tc_names.append(getattr(getattr(tc, "function", None), "name", "?"))
                lines.append(f"[assistant called: {', '.join(tc_names)}]")

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
    ) -> Tuple[str, Dict[str, Any]]:
        """Create, run and close a sub-agent. Returns (result, stats).

        The foreground path. A background run composes the same three steps
        in a different order — see ``_launch_background``.

        Args:
            suppress_output: When True, all live display callbacks are
                suppressed so a background thread does not interleave output
                with the foreground session.
        """
        sub_agent, setup = self._build_sub_agent(suppress_output)
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
            self._close_sub_agent(sub_agent)

    def _build_sub_agent(self, suppress_output: bool) -> Tuple[Any, Dict[str, Any]]:
        """Construct a sub-agent. Returns it with the keyword arguments
        ``_drive_sub_agent`` needs from the same config read.

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

        max_turns = self._definition.get("max_turns", 15)
        agent_name = self._definition["name"]
        step_cb = None if suppress_output else self._make_prefixed_step_callback(max_turns)

        # A foreground sub-agent asks through the parent: the callback prepends
        # "[agent_name]" to the tool name so the user knows which one is asking.
        #
        # A background sub-agent has nobody to ask, and must not read stdin from
        # its thread (that corrupts the terminal's raw mode). So it refuses every
        # call that needs confirmation, through a transport of its own: with no
        # callbacks at all it would get a ``NullTransport``, which approves them
        # all. It can still run whatever permission rules allow outright, and a
        # denial still denies. ``NullTransport`` itself keeps approving, since
        # that is the documented default for a host with no callbacks.
        transport = None
        if suppress_output:
            from ...transport import SdkTransport

            transport = SdkTransport(confirm_tool=lambda *_: False)
            confirm_cb = None
        elif not self._confirmation_callback:
            confirm_cb = None
        else:
            _parent_cb = self._confirmation_callback
            def confirm_cb(tool_name: str, tool_desc: str, tool_args: dict) -> bool:
                return _parent_cb(f"[{agent_name}] {tool_name}", tool_desc, tool_args)

        sub_agent = Agentao(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
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
            skill_manager=_child_skill_manager(
                self._skill_manager_getter, agent_name,
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
            confirmation_callback=confirm_cb,
            step_callback=step_cb,
            output_callback=None if suppress_output else self._output_callback,
            tool_complete_callback=None if suppress_output else self._tool_complete_callback,
            ask_user_callback=None if suppress_output else self._ask_user_callback,
            max_context_tokens=self._max_context_tokens or 200_000,
            # thinking_callback intentionally omitted for sub-agents
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
        for name, tool in list(self._all_tools.items()):
            if requested is not None and name not in requested:
                continue
            try:
                origin = self._tool_origin_getter(name)
            except KeyError:  # removed from the parent since the snapshot
                continue
            if origin == "mcp":
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

        stats = {
            "agent_name": self._definition["name"],
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

    def _launch_background(self, task: str, parent_context: str) -> str:
        agent_id = uuid.uuid4().hex[:8]
        agent_name = self._definition["name"]
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
            sub_agent = None
            try:
                sub_agent, setup = self._build_sub_agent(suppress_output=True)
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
                self._bg_store.update(
                    agent_id,
                    status=state,
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
                        error_type=f"incomplete:{incomplete.reason}",
                    )
                else:
                    self._terminal_subagent_event(
                        subagent_ctx, state, task_summary,
                    )
            except AgentCancelledError:
                # Defensive only — see the foreground site. ``chat()`` does not
                # raise this, so an ordinary cancel is classified above and
                # keeps its partial result and counters. Reaching here means
                # the cancellation came from outside the drive, where there is
                # no run to record.
                self._bg_store.update(agent_id, status="cancelled")
                self._terminal_subagent_event(subagent_ctx, "cancelled", task_summary)
            except Exception as exc:
                self._bg_store.update(agent_id, status="failed", error=str(exc))
                self._terminal_subagent_event(
                    subagent_ctx, "failed", task_summary,
                    error_type=type(exc).__name__,
                )
            finally:
                self._bg_store.unregister_token(agent_id)
                if sub_agent is not None:
                    self._close_sub_agent(sub_agent)

        # Background agents run silently: suppress_output=True ensures no callbacks
        # fire on the background thread, preventing interleaving with foreground output.
        t = threading.Thread(target=_run, daemon=True, name=f"bg-agent-{agent_id}")
        t.start()

        return (
            f"Background agent '{agent_name}' started (ID: {agent_id}). "
            f"Task: {task[:80]}{'…' if len(task) > 80 else ''}. "
            f"Use check_background_agent(agent_id='{agent_id}') to get the result."
        )

    # ------------------------------------------------------------------
    # Progress callback with turn counter
    # ------------------------------------------------------------------

    def _make_prefixed_step_callback(
        self, max_turns: int
    ) -> Optional[Callable]:
        parent_cb = self._step_callback
        if not parent_cb:
            return None
        agent_name = self._definition["name"]
        turn_counter = [0]  # mutable cell

        def prefixed(tool_name: Optional[str], tool_args: dict) -> None:
            if tool_name is None:
                # Called before each LLM iteration — increment turn counter
                turn_counter[0] += 1
                parent_cb(None, tool_args)  # keep the "Thinking…" reset
            else:
                label = f"[{agent_name} {turn_counter[0]}/{max_turns}] {tool_name}"
                parent_cb(label, tool_args)

        return prefixed
