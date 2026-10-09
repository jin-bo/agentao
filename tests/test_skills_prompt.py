"""Skills reach the turn's prompt, in two places.

The **catalogue** (every enabled skill, active or not) is in the system
message — the cached prefix — and the **active skills' bodies** ride the
request-only volatile tail. The split is the point: an activation must leave
the system message byte-identical, or it invalidates the provider's cache over
the whole history.

The gating tests read both halves on purpose. A negative assertion against one
half alone would hold no matter where the block went.
"""

import pytest

from agentao import Agentao

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd", "isolated_skill_dirs")


def test_skills_in_system_prompt(tmp_path):
    """The catalogue lists every skill; an activation adds the active block."""
    agent = _agent_with_a_skill(tmp_path)
    try:
        system_prompt = agent._build_system_prompt() + agent._build_volatile_tail()
        assert "=== Available Skills ===" in system_prompt
        skills = agent.skill_manager.list_available_skills()
        assert skills
        for skill_name in sorted(skills):
            assert skill_name in system_prompt, f"{skill_name} missing from system prompt"

        agent.skill_manager.activate_skill("demo-skill", "Test task")
        system_prompt_after = agent._build_system_prompt() + agent._build_volatile_tail()
        assert "=== Active Skills ===" in system_prompt_after
        assert "demo-skill" in system_prompt_after
    finally:
        agent.close()

# ── the catalogue is an instruction to call a tool (#254) ──────────────────


def _agent_with_a_skill(tmp_path, **kwargs):
    """An agent whose catalogue is exactly one skill, under ``tmp_path``.

    The module's ``isolated_skill_dirs`` keeps the global and bundled skill
    directories under ``tmp_path`` too (see conftest.py).
    """
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


def test_the_catalogue_renders_when_activate_skill_is_registered(tmp_path):
    agent = _agent_with_a_skill(tmp_path)
    try:
        prompt = (agent._build_system_prompt() + agent._build_volatile_tail())
    finally:
        agent.close()

    assert "activate_skill" in agent.tools.tools
    assert "=== Available Skills ===" in prompt
    assert "demo-skill" in prompt


def test_the_catalogue_is_dropped_when_activate_skill_is_not_registered(tmp_path):
    """``disable_tools`` (here), an ``enabled_tools`` allowlist and a
    sub-agent's ``tools:`` list all reach the same state: the block would tell
    the model to "use the activate_skill tool" that it does not have."""
    agent = _agent_with_a_skill(tmp_path, disable_tools={"activate_skill"})
    try:
        prompt = (agent._build_system_prompt() + agent._build_volatile_tail())
    finally:
        agent.close()

    assert "activate_skill" not in agent.tools.tools
    assert "demo-skill" in agent.skill_manager.list_available_skills()
    assert "=== Available Skills ===" not in prompt


def test_the_catalogue_is_in_the_system_message_and_not_in_the_tail(tmp_path):
    agent = _agent_with_a_skill(tmp_path)
    try:
        system = agent._build_system_prompt()
        tail = agent._build_volatile_tail()
    finally:
        agent.close()

    assert "=== Available Skills ===" in system
    assert "demo-skill" in system
    assert "=== Available Skills ===" not in tail


def test_activating_a_skill_leaves_the_system_message_byte_identical(tmp_path):
    """The catalogue lists active skills too. While it listed only the
    inactive ones, every activation rewrote ``messages[0]`` — ahead of the
    whole history in the provider's cached prefix."""
    agent = _agent_with_a_skill(tmp_path)
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


def test_disabling_a_skill_changes_the_catalogue_and_the_tool_enum_together(tmp_path):
    """The one event that does rewrite the catalogue already rewrites the
    ``activate_skill`` enum in the tools block, which is why the catalogue can
    live in the cached prefix at no extra cost.

    This reads the two sources directly. That a *running turn* sees them move
    together rests on the runner serializing the tools block once per turn
    (``chat_loop/_runner.py::run``), which this test does not pin."""
    agent = _agent_with_a_skill(tmp_path)

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


def test_an_active_skill_still_renders_without_the_tool(tmp_path):
    """Only the catalogue is gated. ``/skills activate`` calls the manager
    directly, so a skill can be active for an agent that never had the tool —
    and its instructions have to keep reaching the prompt."""
    agent = _agent_with_a_skill(tmp_path, disable_tools={"activate_skill"})
    try:
        agent.skill_manager.activate_skill("demo-skill", "task")
        prompt = (agent._build_system_prompt() + agent._build_volatile_tail())
    finally:
        agent.close()

    assert "=== Active Skills ===" in prompt
    assert "BODY" in prompt


if __name__ == "__main__":
    test_skills_in_system_prompt()
