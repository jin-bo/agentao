"""Test that skills are included in system prompt."""

import os
from pathlib import Path

from dotenv import load_dotenv

from agentao import Agentao


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
    system_prompt = agent._build_system_prompt()

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
    system_prompt_after = agent._build_system_prompt()
    assert "=== Active Skills ===" in system_prompt_after
    assert activation_target in system_prompt_after

    assert system_prompt.strip()

if __name__ == "__main__":
    test_skills_in_system_prompt()
