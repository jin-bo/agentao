"""Test that reliability principles are present in the system prompt."""

from unittest.mock import Mock, patch


def _make_agent(thinking_callback=None):
    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao
        agent = Agentao(thinking_callback=thinking_callback)
    return agent


def test_reliability_section_present_without_project_instructions():
    """Reliability Principles appear when no CHATAGENT.md is loaded."""
    agent = _make_agent()
    # Force no project instructions
    agent.project_instructions = None
    prompt = agent._build_system_prompt()
    assert "=== Reliability Principles ===" in prompt, (
        "Reliability Principles section must be in prompt (no project instructions)"
    )
    print("✅ Reliability section present without project instructions")


def test_reliability_section_present_with_project_instructions():
    """Reliability Principles appear even when project instructions are loaded."""
    agent = _make_agent()
    agent.project_instructions = "# Project\nUse uv."
    prompt = agent._build_system_prompt()
    assert "=== Reliability Principles ===" in prompt, (
        "Reliability Principles section must be in prompt (with project instructions)"
    )
    print("✅ Reliability section present with project instructions")


def test_reliability_keywords():
    """The four rules contain their key discriminating phrases."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    for phrase in ("assert facts", "differs from what you expected", "returns an error", "Distinguish"):
        assert phrase in prompt, f"Expected phrase not found in reliability section: {phrase!r}"
    print("✅ All four reliability rule keywords present")


def test_reasoning_structure_with_thinking_callback():
    """When thinking_callback is set, reasoning instructions include 'Expectation:' and 'falsifiable'."""
    agent = _make_agent(thinking_callback=lambda x: None)
    prompt = agent._build_system_prompt()
    assert "Expectation:" in prompt, "Reasoning instructions should contain 'Expectation:'"
    assert "falsifiable" in prompt, "Reasoning instructions should contain 'falsifiable'"
    print("✅ Structured reasoning instructions present when thinking_callback is set")


def test_reasoning_structure_absent_without_thinking_callback():
    """When thinking_callback is None, the Reasoning Requirement section is absent."""
    agent = _make_agent(thinking_callback=None)
    prompt = agent._build_system_prompt()
    assert "=== Reasoning Requirement ===" not in prompt, (
        "Reasoning Requirement section should not appear when thinking_callback is None"
    )
    print("✅ Reasoning Requirement section absent when thinking_callback is None")


def test_reliability_before_memories():
    """Reliability Principles section appears before the Memories section (when memories exist)."""
    agent = _make_agent()
    # Inject a memory so the Memories section is rendered
    agent.memory_tool.execute(key="test_key", value="test_value")
    prompt = agent._build_system_prompt()
    rel_idx = prompt.find("=== Reliability Principles ===")
    mem_idx = prompt.find("=== Memories ===")
    assert rel_idx != -1, "Reliability Principles section not found"
    assert mem_idx != -1, "Memories section not found"
    assert rel_idx < mem_idx, (
        f"Reliability Principles (pos {rel_idx}) should appear before Memories (pos {mem_idx})"
    )
    print("✅ Reliability Principles appears before Memories section")


if __name__ == "__main__":
    print("Testing reliability principles in system prompt...")
    print()
    tests = [
        test_reliability_section_present_without_project_instructions,
        test_reliability_section_present_with_project_instructions,
        test_reliability_keywords,
        test_reasoning_structure_with_thinking_callback,
        test_reasoning_structure_absent_without_thinking_callback,
        test_reliability_before_memories,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
        except Exception as e:
            import traceback
            print(f"❌ {t.__name__} (unexpected error): {e}")
            traceback.print_exc()
        print()
    print("=" * 50)
    if passed == len(tests):
        print(f"✅ All {passed} tests passed!")
    else:
        print(f"❌ {passed}/{len(tests)} tests passed")
        exit(1)
