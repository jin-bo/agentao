"""Skills reach the turn's prompt.

Since stage 0a the two skills blocks ride the request-only volatile tail
rather than the system message, so every assertion here reads both. The
negative one especially: against the system message alone it would now
hold no matter what the catalogue did.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agentao import Agentao

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def test_skills_in_system_prompt():
    """Test that available skills are listed in the system prompt."""
    load_dotenv()

    agent = Agentao(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        model=os.getenv("OPENAI_MODEL"),
        working_directory=Path.cwd(),
    )

    print("Testing Skills in System Prompt")
    print("=" * 80)

    # Build system prompt
    system_prompt = (agent._build_system_prompt() + agent._build_volatile_tail())

    print("\n=== SYSTEM PROMPT ===")
    print(system_prompt)
    print("\n" + "=" * 80)

    # Check if skills section exists
    assert "=== Available Skills ===" in system_prompt

    # List available skills
    skills = agent.skill_manager.list_available_skills()
    print(f"\n✅ Found {len(skills)} available skills")
    assert skills

    # Check if each skill is mentioned in the prompt
    print("\n=== Skills Verification ===")
    for skill_name in sorted(skills):
        assert skill_name in system_prompt, f"{skill_name} missing from system prompt"
        print(f"✅ {skill_name} - found in prompt")

    # Test activating a skill and rebuilding prompt
    print("\n=== Testing Skill Activation ===")
    activation_target = "pdf" if "pdf" in skills else sorted(skills)[0]
    result = agent.skill_manager.activate_skill(activation_target, "Test task")
    print(f"Activated skill: {activation_target} -> {result[:100]}...")

    # Rebuild system prompt after activation
    system_prompt_after = (agent._build_system_prompt() + agent._build_volatile_tail())
    assert "=== Active Skills ===" in system_prompt_after
    assert activation_target in system_prompt_after

    assert system_prompt.strip()

# ── the catalogue is an instruction to call a tool (#254) ──────────────────


def _agent_with_a_skill(tmp_path, monkeypatch, **kwargs):
    """An agent whose catalogue is exactly one skill, under ``tmp_path``.

    The global and bundled skill directories are redirected too. Both are
    module-level constants bound at import time from the real home, so
    monkeypatching ``HOME`` does not move them: without this the agent scans
    the developer's own ``~/.agentao/skills`` and ``_bootstrap_bundled_skills``
    copies this repo's ``skills/`` into it, which makes the catalogue
    assertions below depend on whatever that machine happens to hold.
    """
    from agentao.skills import manager as skills_manager

    monkeypatch.setattr(
        skills_manager, "_GLOBAL_SKILLS_DIR", tmp_path / "home" / "skills",
    )
    monkeypatch.setattr(
        skills_manager, "_BUNDLED_SKILLS_DIR", tmp_path / "no-bundled-skills",
    )
    d = tmp_path / ".agentao" / "skills" / "demo-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A demo skill\n---\n\n# demo-skill\n\nBODY\n",
        encoding="utf-8",
    )
    return Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path, **kwargs,
    )


def test_the_catalogue_renders_when_activate_skill_is_registered(tmp_path, monkeypatch):
    agent = _agent_with_a_skill(tmp_path, monkeypatch)
    try:
        prompt = (agent._build_system_prompt() + agent._build_volatile_tail())
    finally:
        agent.close()

    assert "activate_skill" in agent.tools.tools
    assert "=== Available Skills ===" in prompt
    assert "demo-skill" in prompt


def test_the_catalogue_is_dropped_when_activate_skill_is_not_registered(tmp_path, monkeypatch):
    """``disable_tools`` (here), an ``enabled_tools`` allowlist and a
    sub-agent's ``tools:`` list all reach the same state: the block would tell
    the model to "use the activate_skill tool" that it does not have."""
    agent = _agent_with_a_skill(tmp_path, monkeypatch, disable_tools={"activate_skill"})
    try:
        prompt = (agent._build_system_prompt() + agent._build_volatile_tail())
    finally:
        agent.close()

    assert "activate_skill" not in agent.tools.tools
    assert "demo-skill" in agent.skill_manager.list_available_skills()
    assert "=== Available Skills ===" not in prompt


def test_an_active_skill_still_renders_without_the_tool(tmp_path, monkeypatch):
    """Only the catalogue is gated. ``/skills activate`` calls the manager
    directly, so a skill can be active for an agent that never had the tool —
    and its instructions have to keep reaching the prompt."""
    agent = _agent_with_a_skill(tmp_path, monkeypatch, disable_tools={"activate_skill"})
    try:
        agent.skill_manager.activate_skill("demo-skill", "task")
        prompt = (agent._build_system_prompt() + agent._build_volatile_tail())
    finally:
        agent.close()

    assert "=== Active Skills ===" in prompt
    assert "BODY" in prompt


if __name__ == "__main__":
    test_skills_in_system_prompt()
