"""What a sub-agent takes from its parent at spawn, besides the tool registry.

Four decisions, each one fail-closed in its own way: the skill catalogue
(the parent's, through ``child_view``), the memory manager (a transient
store of the child's own), a host tool's copy (only when the tool declares
``copies_to_subagents``), and ``save_memory``'s write target (the parent's
manager). :class:`~._wrapper.AgentToolWrapper` calls them while it builds
and narrows the sub-agent; none of them reads the wrapper's state.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Optional

from ...tools.base import RegistrableTool

logger = logging.getLogger(__name__)


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

    def _no_skills_gated(parent: Any) -> Any:
        # The fallback has no MCP Skills session, so nothing would gate the
        # child's shell — while the parent's context, which the child is
        # briefed with, may carry a loaded skill's instructions. Its origins
        # as of now become the child's restore-only origins: the same gates,
        # failing towards asking (docs/design/mcp-skills.md §6.2).
        child = _no_skills()
        try:
            origins = set(getattr(parent, "mcp_orphan_origins", None) or ())
            mcp_skills = getattr(parent, "mcp_skills", None)
            if mcp_skills is not None:
                origins |= set(mcp_skills.origins(getattr(parent, "mcp_view", None)))
        except Exception:
            origins = {"*"}
        child.mcp_orphan_origins = {o for o in origins if isinstance(o, str)}
        return child

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
        # Not gated, unlike the fallbacks below: a skill manager that cannot
        # be read cannot have loaded an MCP skill either (activation goes
        # through it), so there is nothing to gate — and a ``*`` origin here
        # would mark every sub-agent answer, which a parent with no Skills
        # session withholds whole.
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
        return _no_skills_gated(parent)
    try:
        child = derive()
    except Exception as exc:
        logger.warning(
            "Sub-agent '%s' gets no skills: deriving the parent's catalogue "
            "raised %s: %s",
            agent_name, type(exc).__name__, exc,
        )
        return _no_skills_gated(parent)
    if child is None:
        logger.warning(
            "Sub-agent '%s' gets no skills: %s.child_view() returned None. A "
            "sub-agent is never built with `skill_manager=None`, which would "
            "re-scan the skill directories per spawn and give it a catalogue "
            "the parent does not advertise.",
            agent_name, type(parent).__name__,
        )
        return _no_skills_gated(parent)
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
