"""Test that reliability principles are present in the system prompt."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _make_agent(thinking_callback=None):
    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao
        agent = Agentao(
            thinking_callback=thinking_callback,
            working_directory=Path.cwd(),
        )
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
    """The seven rules contain their key discriminating phrases."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    for phrase in (
        "assert facts",                    # #1
        "differs from what you expected",  # #2
        "returns an error",                # #3
        "Distinguish",                     # #4
        "Never fabricate",                 # #5
        "Report outcomes faithfully",      # #6
        "collaborator, not just an executor",  # #7
    ):
        assert phrase in prompt, f"Expected phrase not found in reliability section: {phrase!r}"
    print("✅ All seven reliability rule keywords present")


def test_reliability_rule_numbering():
    """Rules 1-7 are numbered in order in the Reliability section."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    rel_idx = prompt.find("=== Reliability Principles ===")
    assert rel_idx != -1
    section = prompt[rel_idx:rel_idx + 3000]
    last = -1
    for n in range(1, 8):
        marker = f"\n{n}. "
        pos = section.find(marker)
        assert pos != -1, f"Rule {n} not found in Reliability section"
        assert pos > last, f"Rule {n} appears out of order"
        last = pos
    print("✅ Reliability rules numbered 1-7 in order")


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
    mem_idx = prompt.find("<memory-stable>")
    assert rel_idx != -1, "Reliability Principles section not found"
    assert mem_idx != -1, "Memory stable block not found"
    assert rel_idx < mem_idx, (
        f"Reliability Principles (pos {rel_idx}) should appear before memory block (pos {mem_idx})"
    )
    print("✅ Reliability Principles appears before memory block")


def test_stable_prefix_order():
    """Stable prefix order: Reliability → Operational → <memory-stable>, which
    is where the system message now ends. Protects the prompt-cache prefix
    against future reorder regressions.

    Since stage 0a the skills catalogue is not below <memory-stable> — it is
    not in this message at all. ``test_system_prompt_sections.py`` pins that
    half; asserting it here too would only be an index comparison against a
    marker that is always absent, which is the shape of a test that cannot
    fail."""
    agent = _make_agent()
    agent.memory_tool.execute(key="order_probe", value="v")
    prompt = agent._build_system_prompt()

    rel_idx = prompt.find("=== Reliability Principles ===")
    op_idx = prompt.find("=== Operational Guidelines ===")
    mem_idx = prompt.find("<memory-stable>")

    assert rel_idx != -1, "Reliability section missing"
    assert op_idx != -1, "Operational Guidelines section missing"
    assert mem_idx != -1, "memory-stable block missing"

    assert rel_idx < op_idx < mem_idx, (
        f"Stable-prefix order violated: rel={rel_idx} op={op_idx} mem={mem_idx}"
    )
    print("✅ Stable prefix order: Reliability → Operational → memory-stable")


def test_reasoning_requirement_has_gating_clause():
    """Reasoning Requirement instructs the model to skip the preamble for
    trivial read-only lookups, not demand it on every tool call."""
    agent = _make_agent(thinking_callback=lambda x: None)
    prompt = agent._build_system_prompt()
    assert "Skip this preamble" in prompt, (
        "Reasoning Requirement should contain a gating clause for trivial calls"
    )
    print("✅ Reasoning Requirement gated for trivial read-only lookups")


if __name__ == "__main__":
    print("Testing reliability principles in system prompt...")
    print()
    tests = [
        test_reliability_section_present_without_project_instructions,
        test_reliability_section_present_with_project_instructions,
        test_reliability_keywords,
        test_reliability_rule_numbering,
        test_reasoning_structure_with_thinking_callback,
        test_reasoning_structure_absent_without_thinking_callback,
        test_reliability_before_memories,
        test_stable_prefix_order,
        test_reasoning_requirement_has_gating_clause,
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
