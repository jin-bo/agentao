"""Test that reliability principles are present in the system prompt."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _make_agent(transport=None):
    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao
        agent = Agentao(
            transport=transport,
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


def test_the_reasoning_requirement_section_is_retired():
    """No transport brings back the section ``thinking_callback=`` used to gate.

    Until 0.5.0 a ``=== Reasoning Requirement ===`` block rendered only when
    the deprecated ``thinking_callback=`` constructor kwarg was set. Nothing
    else set the flag: a host on ``transport=`` — the CLI, the ACP server,
    ``agentao run``, and anyone who followed the migration advice — had been
    running without it since the day the Transport protocol landed. The kwarg
    is gone and the section went with it, rather than reappearing for hosts
    that never had it. A transport that *does* handle ``THINKING`` events is
    the case that would bring it back if the gate were ever re-derived.
    """
    from agentao.embedding.compat import build_compat_transport

    for transport in (None, build_compat_transport(thinking_callback=lambda _t: None)):
        prompt = _make_agent(transport=transport)._build_system_prompt()
        assert "=== Reasoning Requirement ===" not in prompt
        assert "Expectation:" not in prompt


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

    The skills catalogue sits between the last two since 0.4.27, but only
    when a described skill is on disk — this agent's skill directories are
    whatever the machine holds. ``test_system_prompt_sections.py`` seeds one
    and pins that half; asserting it here would be an index comparison
    against a marker that may be absent, which is the shape of a test that
    cannot fail."""
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


if __name__ == "__main__":
    print("Testing reliability principles in system prompt...")
    print()
    tests = [
        test_reliability_section_present_without_project_instructions,
        test_reliability_section_present_with_project_instructions,
        test_reliability_keywords,
        test_reliability_rule_numbering,
        test_the_reasoning_requirement_section_is_retired,
        test_reliability_before_memories,
        test_stable_prefix_order,
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
