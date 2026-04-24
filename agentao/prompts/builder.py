"""SystemPromptBuilder — composes the full system prompt for one turn.

Holds *only* the assembly logic. Section text lives in
:mod:`agentao.prompts.sections`; the agent owns runtime state
(skills, memory, todos, plan session, etc.) and is passed in. This
keeps the builder stateless and the agent unaware of the assembly
order — both can change independently.

Behavioral contract preserved verbatim from the previous in-class
implementation in ``agentao/agent.py``:

- Section ordering is unchanged.
- ``_stable_block_chars`` is still written back onto the agent after
  the stable memory block is rendered (the CLI status surface reads
  it via ``getattr(cli.agent, '_stable_block_chars', 0)``).
- ``_extract_context_hints`` is still called as an agent method, since
  tests assert on it directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


class SystemPromptBuilder:
    """Assemble the system prompt by reading state from an ``Agentao``.

    A fresh builder is cheap; the agent re-instantiates one (or reuses
    a single instance — both are valid) on each call to
    ``Agentao._build_system_prompt()``.
    """

    def __init__(self, agent: "Agentao") -> None:
        self._agent = agent

    def build(self) -> str:
        agent = self._agent

        # Project-specific instructions, when AGENTAO.md is present.
        if agent.project_instructions:
            prompt = (
                f"=== Project Instructions ===\n\n"
                f"{agent.project_instructions}\n\n"
                f"=== Agent Instructions ===\n\n"
            )
        else:
            prompt = ""

        # --- Stable prefix (cached across turns) ---------------------------
        # Order: Identity → Reliability → Task Classification → Execution
        # Protocol → Completion Standard → Untrusted Input → Operational
        # Guidelines → (Reasoning) → (Agents) → <memory-stable>. Volatile
        # content (skills, todos, dynamic recall, plan suffix) lives below
        # this prefix to maximize prompt-cache reuse across turns.
        prompt += build_identity_section(agent.working_directory)
        prompt += build_reliability_section()
        prompt += build_task_classification_section()
        prompt += build_execution_protocol_section()
        prompt += build_completion_standard_section()
        prompt += build_untrusted_input_section()
        prompt += build_operational_guidelines(plan_mode=agent._plan_mode)

        if agent._has_thinking_handler:
            prompt += self._reasoning_requirement_block()

        # Available agents (suppressed in plan mode — delegation contradicts
        # research-only intent).
        if not agent._plan_mode and agent.agent_manager:
            prompt += self._available_agents_block()

        # Stable memory block — last item in the stable prefix.
        stable_records = agent.memory_manager.get_stable_entries()
        cross_session_tail = agent.memory_manager.get_cross_session_tail()
        stable_block = agent.memory_renderer.render_stable_block(
            stable_records, session_tail=cross_session_tail,
        )
        agent._stable_block_chars = len(stable_block)
        if stable_block:
            prompt += "\n\n" + stable_block

        # --- Volatile suffix (changes within a session) --------------------
        prompt += self._available_skills_block()

        skills_context = agent.skill_manager.get_skills_context()
        if skills_context:
            prompt += "\n\n" + skills_context

        prompt += self._todos_block()

        # Dynamic recall (per-turn; query-specific top-k candidates).
        # Exclude entries already shown in the stable block to avoid duplication.
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
                prompt += "\n\n" + recall_block

        if agent._plan_mode:
            prompt += build_plan_prompt(agent._plan_session)

        return prompt

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
