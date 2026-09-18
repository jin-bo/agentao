"""Skills reach the turn's prompt, in two places.

The **catalogue** (every enabled skill, active or not) is in the system
message — the cached prefix — and the **active skills' bodies** ride the
request-only volatile tail. The split is the point: an activation must leave
the system message byte-identical, or it invalidates the provider's cache over
the whole history.

The gating tests read both halves on purpose. A negative assertion against one
half alone would hold no matter where the block went.
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


def test_the_catalogue_is_in_the_system_message_and_not_in_the_tail(tmp_path, monkeypatch):
    agent = _agent_with_a_skill(tmp_path, monkeypatch)
    try:
        system = agent._build_system_prompt()
        tail = agent._build_volatile_tail()
    finally:
        agent.close()

    assert "=== Available Skills ===" in system
    assert "demo-skill" in system
    assert "=== Available Skills ===" not in tail


def test_activating_a_skill_leaves_the_system_message_byte_identical(tmp_path, monkeypatch):
    """The catalogue lists active skills too. While it listed only the
    inactive ones, every activation rewrote ``messages[0]`` — ahead of the
    whole history in the provider's cached prefix."""
    agent = _agent_with_a_skill(tmp_path, monkeypatch)
    try:
        before = agent._build_system_prompt()
        tail_before = agent._build_volatile_tail()

        agent.skill_manager.activate_skill("demo-skill", "task")
        active = agent._build_system_prompt()
        tail_active = agent._build_volatile_tail()

        agent.skill_manager.deactivate_skill("demo-skill")
        after = agent._build_system_prompt()
        tail_after = agent._build_volatile_tail()
    finally:
        agent.close()

    assert "demo-skill" in before
    assert active == before
    assert after == before
    # What activation changes is the tail, and only the tail.
    assert "=== Active Skills ===" not in tail_before
    assert "=== Active Skills ===" in tail_active and "BODY" in tail_active
    assert tail_after == tail_before


def test_disabling_a_skill_changes_the_catalogue_and_the_tool_enum_together(tmp_path, monkeypatch):
    """The one event that does rewrite the catalogue already rewrites the
    ``activate_skill`` enum in the tools block, which is why the catalogue can
    live in the cached prefix at no extra cost. Both are read at the turn
    boundary, so that is where they are compared."""
    agent = _agent_with_a_skill(tmp_path, monkeypatch)

    def enum():
        return agent.tools.tools["activate_skill"].parameters[
            "properties"]["skill_name"].get("enum", [])

    try:
        assert "demo-skill" in enum()
        assert "• demo-skill:" in agent._build_system_prompt()

        agent.skill_manager.disable_skill("demo-skill")

        assert "demo-skill" not in enum()
        assert "• demo-skill:" not in agent._build_system_prompt()
    finally:
        agent.close()


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
