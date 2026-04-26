"""SystemPromptBuilder — composes the full system prompt for one turn.

Holds *only* the assembly logic. Section text lives in
:mod:`agentao.prompts.sections`; the agent owns runtime state
(skills, memory, todos, plan session, etc.) and is passed in. This
keeps the builder stateless and the agent unaware of the assembly
order — both can change independently.

Behavioral contract:

- Section ordering follows the documented stable-prefix / volatile-suffix
  layout for prompt-cache reuse.
- ``_stable_block_chars`` is written onto the agent after the stable
  memory block is rendered (the CLI status surface reads it via
  ``getattr(cli.agent, '_stable_block_chars', 0)``).
- ``_extract_context_hints`` is called as an agent method, since tests
  assert on it directly.

Per-section token diagnostics are emitted as a single ``prompt_sections``
log line (logger ``agentao.prompt_diag``) on every build. Token counts
are cached on the agent keyed by section text so unchanged sections
(notably the stable prefix) are not re-tokenized every turn.
Diagnostics are best-effort — if estimation fails the build still
returns normally.
"""

from __future__ import annotations

import json
import logging
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


class SystemPromptBuilder:
    """Assemble the system prompt by reading state from an ``Agentao``.

    A fresh builder is cheap; the agent re-instantiates one (or reuses
    a single instance — both are valid) on each call to
    ``Agentao._build_system_prompt()``.
    """

    def __init__(self, agent: "Agentao") -> None:
        self._agent = agent

    def build(self) -> str:
        sections = self._build_sections()
        prompt = "".join(sections.values())
        self._log_section_diagnostics(sections)
        return prompt

    def _build_sections(self) -> Dict[str, str]:
        """Build each named section. Returns insertion-ordered dict.

        Order: project_instructions (optional) → stable prefix
        (identity, reliability, task_classification, execution_protocol,
        completion_standard, untrusted_input, operational_guidelines,
        reasoning_requirement?, available_agents?, stable_memory?) →
        volatile suffix (available_skills?, active_skills_context?,
        todos?, dynamic_recall?, plan_prompt?).

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
        # Volatile content (skills, todos, dynamic recall, plan suffix)
        # lives below this prefix to maximize prompt-cache reuse.
        sections["identity"] = build_identity_section(agent.working_directory)
        sections["reliability"] = build_reliability_section()
        sections["task_classification"] = build_task_classification_section()
        sections["execution_protocol"] = build_execution_protocol_section()
        sections["completion_standard"] = build_completion_standard_section()
        sections["untrusted_input"] = build_untrusted_input_section()
        sections["operational_guidelines"] = build_operational_guidelines(
            plan_mode=agent._plan_mode
        )

        if agent._has_thinking_handler:
            sections["reasoning_requirement"] = self._reasoning_requirement_block()

        # Available agents (suppressed in plan mode — delegation contradicts
        # research-only intent).
        if not agent._plan_mode and agent.agent_manager:
            agents_block = self._available_agents_block()
            if agents_block:
                sections["available_agents"] = agents_block

        # Stable memory block — last item in the stable prefix. Writes
        # ``_stable_block_chars`` onto the agent so the CLI status surface
        # can read it without re-rendering.
        stable_records = agent.memory_manager.get_stable_entries()
        cross_session_tail = agent.memory_manager.get_cross_session_tail()
        stable_block = agent.memory_renderer.render_stable_block(
            stable_records, session_tail=cross_session_tail,
        )
        agent._stable_block_chars = len(stable_block)
        if stable_block:
            sections["stable_memory"] = "\n\n" + stable_block

        # --- Volatile suffix (changes within a session) --------------------
        skills_block = self._available_skills_block()
        if skills_block:
            sections["available_skills"] = skills_block

        skills_context = agent.skill_manager.get_skills_context()
        if skills_context:
            sections["active_skills_context"] = "\n\n" + skills_context

        todos_block = self._todos_block()
        if todos_block:
            sections["todos"] = todos_block

        # Dynamic recall (per-turn; query-specific top-k candidates).
        # Exclude entries already shown in the stable block to avoid
        # duplication.
        context_hints = agent._extract_context_hints()
        stable_ids = {r.id for r in stable_records}
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

    def _log_section_diagnostics(self, sections: Dict[str, str]) -> None:
        """Log per-section token counts. Best-effort — never raises.

        Stable-prefix sections are byte-identical across turns, so
        results are memoized on the agent (keyed by section name +
        text) to avoid re-tokenizing them on every build.
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
                "prompt_sections total_tokens=%d breakdown=%s",
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
        skill_manager = self._agent.skill_manager
        available_skills = skill_manager.list_available_skills()
        active_names = set(skill_manager.get_active_skills().keys())
        inactive_skills = [s for s in available_skills if s not in active_names]
        if not inactive_skills:
            return ""
        out = "\n\n=== Available Skills ===\n"
        out += "You have access to specialized skills. Use the 'activate_skill' tool to activate them when needed.\n\n"
        for skill_name in sorted(inactive_skills):
            skill_info = skill_manager.get_skill_info(skill_name)
            if skill_info:
                description = skill_info.get('description', 'No description available')
                when_to_use = skill_info.get('when_to_use', '')
                out += f"• {skill_name}: {description}\n"
                if when_to_use:
                    out += f"  Activate when: {when_to_use}\n"
        out += "\nWhen the user's request matches a skill's description, use the activate_skill tool before proceeding with the task."
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
