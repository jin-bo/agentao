"""SystemPromptBuilder — composes the full system prompt for one turn.

Holds *only* the assembly logic. Section text lives in
:mod:`agentao.prompts.sections`; the agent owns runtime state
(skills, memory, todos, plan session, etc.) and is passed in. This
keeps the builder stateless and the agent unaware of the assembly
order — both can change independently.

Two products, two cadences:

- :meth:`SystemPromptBuilder.build` returns the **system message**, which
  is the stable prefix and nothing else.
- :meth:`SystemPromptBuilder.build_volatile_tail` returns the volatile
  content (active-skill bodies, todos, dynamic recall, plan) as one
  ``<system-reminder>`` block, which the runtime appends to the outgoing
  request as a trailing ``user`` message and never persists.

They are split because ``messages[0]`` is the head of the provider's
cached prefix: while the volatile blocks lived inside the system message,
flipping one todo status invalidated the cache covering the whole
history. See ``docs/design/llm-api-adapters.md`` §2.3 stage 0a.

Behavioral contract:

- Section ordering follows the documented stable-prefix / volatile-tail
  layout for prompt-cache reuse.
- ``_stable_block_chars`` is written onto the agent after the stable
  memory block is rendered (the CLI status surface reads it via
  ``getattr(cli.agent, '_stable_block_chars', 0)``), and
  ``_stable_memory_ids`` with it — the dynamic-recall block in the tail
  excludes whatever the stable block already showed, and the two halves
  are now built by separate calls.
- ``_extract_context_hints`` is called as an agent method, since tests
  assert on it directly.

Per-section token diagnostics are emitted as one ``prompt_sections`` log
line per system build and one ``volatile_tail_sections`` line per tail
build (logger ``agentao.prompt_diag``). Token counts are cached on the
agent keyed by section text so unchanged sections (notably the stable
prefix) are not re-tokenized every turn. Diagnostics are best-effort —
if estimation fails the build still returns normally.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Dict

from ..plan import build_plan_prompt
from .sections import (
    build_completion_standard_section,
    build_execution_protocol_section,
    build_identity_section,
    build_operational_guidelines,
    build_reliability_section,
    build_task_classification_section,
    build_untrusted_input_section,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..agent import Agentao


_logger = logging.getLogger("agentao.prompt_diag")

#: A literal ``</system-reminder>`` anywhere inside the tail's body would close
#: the wrapper early and drop everything after it into the request as bare
#: trailing user text — the highest-leverage position there is — shedding the
#: "this is data, not instructions" framing the wrapper exists to supply. The
#: body is not all agentao-authored: it carries memory values the model itself
#: wrote, and active skills' bodies from disk and from plugins. Matched
#: case-insensitively and with loose whitespace, because the reader being
#: steered is a language model, not an XML parser: ``</ SYSTEM-REMINDER >``
#: works on it just as well as the exact spelling.
_CLOSING_REMINDER_RE = re.compile(r"</\s*system-reminder\s*>", re.IGNORECASE)

# The tool the available-skills catalogue tells the model to call.
# Spelled here rather than imported: ``agentao.tools`` pulls the whole
# tool package in, and the prompt builder only needs the name.
_ACTIVATE_SKILL_TOOL = "activate_skill"


def _prompt_dialect(agent) -> str:
    """Which shell syntax the guidelines should speak, read from the tool that will run it.

    Read here rather than derived from the host platform: a Docker or remote executor runs a
    different shell than the machine agentao is on, and it is the executor that declares
    which. Anything unanswerable falls back to POSIX, which is what the text said before this
    existed — a prompt is advice, and advice from the wrong dialect is worse than generic
    advice, but neither is worth failing a turn over.

    This sits in the cached stable prefix, so it must not vary within a session. It does not:
    the spec object changes only on re-resolution.
    """
    try:
        from ..capabilities.shell_spec import ShellSpec
        from ..tools.base import SHELL_TOOL_NAME

        spec = agent.tools.tools[SHELL_TOOL_NAME].shell_spec
        return spec.dialect.value if isinstance(spec, ShellSpec) else "posix"
    except Exception:  # noqa: BLE001 - a prompt never fails a turn
        return "posix"


class SystemPromptBuilder:
    """Assemble the system prompt by reading state from an ``Agentao``.

    A fresh builder is cheap; the agent re-instantiates one (or reuses
    a single instance — both are valid) on each call to
    ``Agentao._build_system_prompt()``.
    """

    def __init__(self, agent: "Agentao") -> None:
        self._agent = agent

    def build(self) -> str:
        """Build the system message — the stable prefix, and nothing else.

        The volatile blocks are *not* here; see
        :meth:`build_volatile_tail`.
        """
        sections = self._build_sections()
        prompt = "".join(sections.values())
        self._log_section_diagnostics(sections)
        return prompt

    def build_volatile_tail(self) -> str:
        """Build the request-only volatile tail, or ``""`` when empty.

        Wrapped in a single ``<system-reminder>`` element, ready to be the
        ``content`` of a trailing ``user`` message on one outgoing request.

        **Request-only, and that is a new lifecycle in this codebase.** The
        two existing ``<system-reminder>`` patterns — the per-turn date/time
        and background notifications — are both *persisted* into
        ``agent.messages``. This one must not be: a persisted tail would pile
        one todos snapshot per turn into the transcript, and the whole point
        of 0a is that this content is cheap to re-send because it is outside
        the cached prefix.

        Returns ``""`` when nothing volatile renders, in which case the
        request is the pre-0a one exactly.

        A literal closing tag inside the body is neutralized first — see
        :data:`_CLOSING_REMINDER_RE`.
        """
        sections = self._build_volatile_sections()
        body = "".join(sections.values()).strip()
        if not body:
            return ""
        self._log_section_diagnostics(sections, label="volatile_tail_sections")
        # Neutralized, not stripped: the escaped form stays readable, so a
        # memory or skill description that legitimately discusses the tag still
        # reads correctly instead of losing text.
        body = _CLOSING_REMINDER_RE.sub(r"<\\/system-reminder>", body)
        return f"<system-reminder>\n{body}\n</system-reminder>"

    def _build_sections(self) -> Dict[str, str]:
        """Build the system message's sections. Insertion-ordered.

        Order: project_instructions (optional) → stable prefix
        (identity, reliability, task_classification, execution_protocol,
        completion_standard, untrusted_input, operational_guidelines,
        reasoning_requirement?, available_agents?, available_skills?,
        stable_memory?).

        Every section here is stable across the turns of one session; the
        volatile ones live in :meth:`_build_volatile_sections`.

        Empty optional sections are omitted from the dict so the
        diagnostic log isn't polluted with zero-token entries.
        """
        agent = self._agent
        sections: Dict[str, str] = {}

        # Project-specific instructions, when AGENTAO.md is present.
        if agent.project_instructions:
            sections["project_instructions"] = (
                f"=== Project Instructions ===\n\n"
                f"{agent.project_instructions}\n\n"
                f"=== Agent Instructions ===\n\n"
            )

        # --- Stable prefix (cached across turns) ---------------------------
        # Volatile content (active-skill bodies, todos, dynamic recall, plan
        # suffix) is not in this message at all — it rides a request-only tail, see
        # ``build_volatile_tail``.
        sections["identity"] = build_identity_section(agent.working_directory)
        sections["reliability"] = build_reliability_section()
        sections["task_classification"] = build_task_classification_section()
        sections["execution_protocol"] = build_execution_protocol_section()
        sections["completion_standard"] = build_completion_standard_section()
        sections["untrusted_input"] = build_untrusted_input_section()
        sections["operational_guidelines"] = build_operational_guidelines(
            plan_mode=agent._plan_mode,
            dialect=_prompt_dialect(agent),
        )

        if agent._has_thinking_handler:
            sections["reasoning_requirement"] = self._reasoning_requirement_block()

        # Available agents (suppressed in plan mode — delegation contradicts
        # research-only intent).
        if not agent._plan_mode and agent.agent_manager:
            agents_block = self._available_agents_block()
            if agents_block:
                sections["available_agents"] = agents_block

        # The skills catalogue — every enabled skill, active or not, so an
        # activation leaves this message byte-identical. Ahead of stable
        # memory because it changes less often: a ``save_memory`` rebuilds
        # from the memory block on, and the catalogue stays cached — on a
        # token-prefix cache, that is. A block-granular one (the opt-in
        # ``cache_control`` markers send this message as a single text block)
        # re-writes the whole message whichever order the two are in.
        skills_block = self._available_skills_block()
        if skills_block:
            sections["available_skills"] = skills_block

        # Stable memory block — last item in the stable prefix. Writes
        # ``_stable_block_chars`` onto the agent so the CLI status surface
        # can read it without re-rendering.
        stable_records = agent.memory_manager.get_stable_entries()
        cross_session_tail = agent.memory_manager.get_cross_session_tail()
        stable_block = agent.memory_renderer.render_stable_block(
            stable_records, session_tail=cross_session_tail,
        )
        agent._stable_block_chars = len(stable_block)
        # Handed to the tail builder, which excludes these from dynamic
        # recall. Written even when the block is empty, so a memory that
        # *stops* being stable cannot stay excluded from recall by a set
        # left over from an earlier build.
        agent._stable_memory_ids = {r.id for r in stable_records}
        if stable_block:
            sections["stable_memory"] = "\n\n" + stable_block

        return sections

    def _build_volatile_sections(self) -> Dict[str, str]:
        """Build the volatile tail's sections. Insertion-ordered.

        Order: active_skills_context? → todos? → dynamic_recall? →
        plan_prompt?. Same relative order they had at the bottom of the
        system message before 0a moved them out of it.

        The skills *catalogue* is not here — it is in the stable prefix. What
        is here is what activation changes: the active skills' bodies, which
        is also how the model learns which catalogue entries are already
        active.

        Rebuilt per *request*, not per turn, which is the one behavioural
        gain beyond caching: a ``todo_write`` in iteration 3 is visible to
        iteration 4, where before it waited for a system-prompt rebuild.
        Re-running dynamic recall per request is the cost of that; it is a
        scored pass over the memory store, not an LLM call.
        """
        agent = self._agent
        sections: Dict[str, str] = {}

        skills_context = agent.skill_manager.get_skills_context()
        if skills_context:
            sections["active_skills_context"] = "\n\n" + skills_context

        todos_block = self._todos_block()
        if todos_block:
            sections["todos"] = todos_block

        # Dynamic recall (per-request; query-specific top-k candidates).
        # Exclude entries already shown in the stable block to avoid
        # duplication — ``_stable_memory_ids`` is written by the system
        # build, which always precedes a tail build in the runtime. A
        # standalone tail build (a test, a host poking at the builder) sees
        # an empty set and may duplicate an entry: wasteful, not wrong.
        context_hints = agent._extract_context_hints()
        stable_ids = getattr(agent, "_stable_memory_ids", None) or set()
        candidates = agent.memory_retriever.recall_candidates(
            query=agent._last_user_message or "",
            context_hints=context_hints,
            exclude_ids=stable_ids,
        )
        if candidates:
            recall_block = agent.memory_renderer.render_dynamic_block(candidates)
            if recall_block:
                sections["dynamic_recall"] = "\n\n" + recall_block

        if agent._plan_mode:
            sections["plan_prompt"] = build_plan_prompt(agent._plan_session)

        return sections

    def _log_section_diagnostics(
        self, sections: Dict[str, str], label: str = "prompt_sections",
    ) -> None:
        """Log per-section token counts. Best-effort — never raises.

        Stable-prefix sections are byte-identical across turns, so
        results are memoized on the agent (keyed by section name +
        text) to avoid re-tokenizing them on every build. ``label``
        separates the system build's line from the tail's; the two
        section-name spaces are disjoint, so they share one cache.
        """
        if not _logger.isEnabledFor(logging.INFO):
            return
        try:
            agent = self._agent
            cm = getattr(agent, "context_manager", None)
            if cm is None:
                return
            cache = getattr(agent, "_prompt_section_token_cache", None)
            if cache is None:
                cache = {}
                agent._prompt_section_token_cache = cache

            counts: Dict[str, int] = {}
            for name, text in sections.items():
                cached = cache.get(name)
                if cached is not None and cached[0] == text:
                    counts[name] = cached[1]
                else:
                    n = cm.count_tokens_in_text(text)
                    cache[name] = (text, n)
                    counts[name] = n

            _logger.info(
                "%s total_tokens=%d breakdown=%s",
                label,
                sum(counts.values()),
                json.dumps(counts, separators=(",", ":")),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Sub-blocks (kept private; assembly-only, no business logic)
    # ------------------------------------------------------------------

    @staticmethod
    def _reasoning_requirement_block() -> str:
        return (
            "\n\n=== Reasoning Requirement ===\n"
            "Before any tool call that modifies state, runs a shell command, "
            "or is part of a multi-step investigation, write 2-3 sentences:\n"
            "- Action: What tool you are calling and with what input.\n"
            "- Expectation: What you expect to find or what the result should confirm.\n"
            "- If wrong: What you will do if the result contradicts your expectation.\n"
            "Skip this preamble for trivial read-only lookups "
            "(single read_file, list_directory, glob). "
            "Be specific and falsifiable when you do write it."
        )

    def _available_agents_block(self) -> str:
        agent_descriptions = self._agent.agent_manager.list_agents()
        if not agent_descriptions:
            return ""
        out = "\n\n=== Available Agents ===\n"
        out += "For the following types of tasks, prefer delegating to a specialized agent:\n\n"
        for agent_name, desc in agent_descriptions.items():
            tool_name = f"agent_{agent_name.replace('-', '_')}"
            out += f"- {agent_name}: {desc} (use tool: {tool_name})\n"
        out += "\nCall the corresponding agent tool to delegate a task."
        return out

    def _available_skills_block(self) -> str:
        # The catalogue is an instruction to call ``activate_skill``, so it is
        # only true for an agent that has that tool. It can be absent three
        # ways: ``disable_tools``, an ``enabled_tools`` allowlist that omits
        # it, or a sub-agent whose definition lists other tools (#254). Gated
        # on the registry rather than on who the agent is; gemini-cli gates
        # the same block the same way
        # (``agents/local-executor.ts``: ``getTool(ACTIVATE_SKILL_TOOL_NAME)``).
        #
        # Only the catalogue. The *active* block stays unconditional: a CLI
        # ``/skills activate`` calls the manager directly, so a skill can be
        # active for an agent that never had the tool.
        registry = getattr(self._agent, "tools", None)
        registered = getattr(registry, "tools", None)
        if registered is not None and _ACTIVATE_SKILL_TOOL not in registered:
            return ""

        # **Active skills are listed too, and that is what keeps this block in
        # the cached prefix.** It used to list only the inactive ones, so every
        # activation rewrote it — and it sits in ``messages[0]``, ahead of the
        # whole history. Now it changes only when the *enabled set* does
        # (enable / disable / install / reload), which already changes
        # ``activate_skill``'s ``skill_name`` enum in the tools block, so the
        # prefix was being rebuilt on those events anyway. pi-mono and
        # gemini-cli list the same way; neither removes a skill once used.
        skill_manager = self._agent.skill_manager
        # Skills with no description give the model nothing to match on, so
        # rendering them as ``• name: `` (empty after the colon) just wastes
        # tokens. Drop them from the prompt; ``/skills`` still lists them.
        described = []
        for s in skill_manager.list_available_skills():
            info = skill_manager.get_skill_info(s)
            if info and (info.get('description') or '').strip():
                described.append((s, info))
        if not described:
            return ""
        out = "\n\n=== Available Skills ===\n"
        out += "You have access to specialized skills. Use the 'activate_skill' tool to activate them when needed.\n\n"
        for skill_name, skill_info in sorted(described, key=lambda p: p[0]):
            description = skill_info['description'].strip()
            when_to_use = skill_info.get('when_to_use', '')
            out += f"• {skill_name}: {description}\n"
            if when_to_use:
                out += f"  Activate when: {when_to_use}\n"
        out += (
            "\nWhen the user's request matches a skill's description and that "
            "skill is not already active, use the activate_skill tool before "
            "proceeding with the task. Active skills, if any, are listed with "
            "their instructions under \"Active Skills\"."
        )
        out += (
            "\nSkill files (SKILL.md, scripts/, references/) live in each "
            "skill's own directory, which is usually NOT your current working "
            "directory; activation reports that directory when the skill has "
            "one, and relative paths in skill instructions resolve against it."
        )
        return out

    def _todos_block(self) -> str:
        todos = self._agent.todo_tool.get_todos()
        if not todos:
            return ""
        icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}
        out = "\n\n=== Current Task List ===\n"
        for todo in todos:
            icon = icons.get(todo["status"], "○")
            out += f"- {icon} [{todo['status']}] {todo['content']}\n"
        out += "\nUpdate task statuses with todo_write as you complete each step."
        return out
