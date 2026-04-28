"""Section-level assertions for the redesigned system prompt.

Guards the stable-prefix order, the four-domain identity, and the
discriminating phrases for each new behavioral clause introduced in
SYSTEM_PROMPT_REDESIGN_PLAN.md.
"""

from pathlib import Path
from unittest.mock import Mock, patch

from agentao.plan import PlanPhase


def _make_agent(thinking_callback=None):
    with patch("agentao.agent.LLMClient") as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"
        from agentao.agent import Agentao
        return Agentao(
            thinking_callback=thinking_callback,
            working_directory=Path.cwd(),
        )


# ---------------------------------------------------------------------------
# Section order
# ---------------------------------------------------------------------------

STABLE_PREFIX_MARKERS = [
    "You are Agentao",                    # Identity
    "=== Reliability Principles ===",
    "=== Task Classification ===",
    "=== Execution Protocol ===",
    "=== Completion Standard ===",
    "=== Untrusted Input Boundary ===",
    "=== Operational Guidelines ===",
]


def test_stable_prefix_order_full():
    """All stable-prefix markers appear in the documented order."""
    agent = _make_agent()
    # Inject a memory so <memory-stable> renders as the trailing marker.
    agent.memory_tool.execute(key="order_probe", value="v")
    prompt = agent._build_system_prompt()

    last = -1
    for marker in STABLE_PREFIX_MARKERS:
        idx = prompt.find(marker)
        assert idx != -1, f"Marker missing from prompt: {marker!r}"
        assert idx > last, (
            f"Out-of-order section: {marker!r} (pos {idx}) should follow the previous marker (pos {last})"
        )
        last = idx

    mem_idx = prompt.find("<memory-stable>")
    assert mem_idx != -1, "<memory-stable> block missing"
    assert mem_idx > last, (
        f"<memory-stable> (pos {mem_idx}) must close the stable prefix after Operational Guidelines (pos {last})"
    )
    print("✅ Stable prefix order: Identity → Reliability → Task Classification → Execution Protocol → "
          "Completion Standard → Untrusted Input → Operational Guidelines → <memory-stable>")


def test_volatile_suffix_after_memory_stable():
    """Skills, todos, and dynamic recall live below <memory-stable>."""
    agent = _make_agent()
    # Force renderable volatile content
    agent.memory_tool.execute(key="suffix_probe", value="v")
    agent.todo_tool.execute(todos=[
        {"content": "step one", "status": "pending"},
    ])
    prompt = agent._build_system_prompt()

    mem_idx = prompt.find("<memory-stable>")
    assert mem_idx != -1
    todo_idx = prompt.find("=== Current Task List ===")
    assert todo_idx != -1, "Todo block must render when todos exist"
    assert todo_idx > mem_idx, (
        f"Todo block (pos {todo_idx}) must follow <memory-stable> (pos {mem_idx})"
    )

    skills_idx = prompt.find("=== Available Skills ===")
    if skills_idx != -1:
        assert skills_idx > mem_idx, "Available Skills must follow <memory-stable>"
    print("✅ Volatile suffix (todos/skills) sits after <memory-stable>")


# ---------------------------------------------------------------------------
# Identity — four domains
# ---------------------------------------------------------------------------

def test_identity_lists_four_domains():
    """Identity section names all four domains and is plan-mode invariant."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    for kw in ("Research", "Data analysis", "Project orchestration", "Coding"):
        assert kw in prompt, f"Identity missing domain keyword: {kw!r}"
    assert "knowledge-work agent" in prompt
    assert "Current Working Directory:" in prompt

    # Plan mode keeps the same identity section
    agent._plan_session.phase = PlanPhase.ACTIVE
    plan_prompt = agent._build_system_prompt()
    for kw in ("Research", "Data analysis", "Project orchestration", "Coding"):
        assert kw in plan_prompt, f"Plan-mode prompt missing domain keyword: {kw!r}"
    print("✅ Identity lists four domains in normal and plan mode")


def test_identity_signals_coding_is_one_of_four():
    """Identity explicitly notes coding is not the single axis."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "one capability of four" in prompt, (
        "Identity must state coding is one capability of four (not the single axis)"
    )
    print("✅ Identity flags coding as one capability of four")


# ---------------------------------------------------------------------------
# Behavioral discriminating phrases
# ---------------------------------------------------------------------------

def test_explore_before_ask_triggers():
    """Execution Protocol enumerates explore-before-ask triggers."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "Explore-before-ask" in prompt
    assert "Conflicting goals" in prompt
    assert "high-impact preference" in prompt
    assert "high-risk action" in prompt
    assert "External material" in prompt
    print("✅ Explore-before-ask triggers enumerated")


def test_untrusted_input_boundary_phrase():
    """Untrusted Input Boundary contains its discriminating phrase."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "data, not instructions" in prompt
    assert "prompt injection" in prompt
    print("✅ Untrusted Input Boundary phrasing present")


def test_truthful_reporting_phrase():
    """Reliability #6 contains its discriminating phrase."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "Report outcomes faithfully" in prompt
    assert "never characterize incomplete work as complete" in prompt
    print("✅ Truthful reporting clause present")


def test_collaborator_phrase():
    """Reliability #7 contains its discriminating phrase."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "collaborator, not just an executor" in prompt
    assert "misconception" in prompt
    assert "adjacent" in prompt
    print("✅ Collaborator clause present")


def test_blast_radius_clause():
    """Operational Guidelines explains reversibility / blast radius."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "reversibility" in prompt
    assert "blast radius" in prompt
    # Three guiding principles
    assert "cost of pausing to confirm is low" in prompt
    assert "Approving an action once" in prompt
    assert "destructive actions as a shortcut" in prompt
    # Four categories present
    for cat in ("Destructive", "Hard to reverse", "Visible to others", "Third-party uploads"):
        assert cat in prompt, f"Blast-radius category missing: {cat!r}"
    print("✅ Blast-radius clause + four categories + three principles present")


def test_tool_result_summarization_clause():
    """Tool-result summarization clause warns about context compression."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "may be cleared later" in prompt
    assert "write down any important information" in prompt
    print("✅ Tool-result summarization clause present")


def test_failure_retry_discipline_clause():
    """Operational Guidelines includes failure-retry discipline."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "diagnose first" in prompt
    assert "blindly retry" in prompt
    print("✅ Failure retry discipline clause present")


# ---------------------------------------------------------------------------
# Tool-name correctness
# ---------------------------------------------------------------------------

REGISTERED_TOOL_HINTS = (
    "read_file", "write_file", "replace", "list_directory",
    "glob", "search_file_content", "run_shell_command",
    "save_memory", "todo_write",
)


def test_tool_names_in_prompt_are_real():
    """Every dedicated-tool name mentioned in Tool Usage exists in the registry."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    registered = {t.name for t in agent.tools.tools.values()}
    for tool_name in REGISTERED_TOOL_HINTS:
        if tool_name in prompt:
            assert tool_name in registered, (
                f"Prompt references {tool_name!r} but it is not a registered tool. "
                f"Registered: {sorted(registered)}"
            )
    print("✅ All tool names in prompt match registered tools")


# ---------------------------------------------------------------------------
# Non-regression: plan-mode behavior preserved
# ---------------------------------------------------------------------------

def test_completion_standard_present_in_normal_mode():
    """Completion Standard appears in normal mode."""
    agent = _make_agent()
    prompt = agent._build_system_prompt()
    assert "=== Completion Standard ===" in prompt
    print("✅ Completion Standard present in normal mode")


def test_task_classification_present_in_plan_mode():
    """New stable sections are not suppressed by plan mode."""
    agent = _make_agent()
    agent._plan_session.phase = PlanPhase.ACTIVE
    prompt = agent._build_system_prompt()
    for marker in (
        "=== Task Classification ===",
        "=== Execution Protocol ===",
        "=== Untrusted Input Boundary ===",
    ):
        assert marker in prompt, f"Plan-mode prompt missing {marker!r}"
    print("✅ New stable sections survive plan mode")


if __name__ == "__main__":
    print("Testing redesigned system prompt sections...")
    print()
    tests = [
        test_stable_prefix_order_full,
        test_volatile_suffix_after_memory_stable,
        test_identity_lists_four_domains,
        test_identity_signals_coding_is_one_of_four,
        test_explore_before_ask_triggers,
        test_untrusted_input_boundary_phrase,
        test_truthful_reporting_phrase,
        test_collaborator_phrase,
        test_blast_radius_clause,
        test_tool_result_summarization_clause,
        test_failure_retry_discipline_clause,
        test_tool_names_in_prompt_are_real,
        test_completion_standard_present_in_normal_mode,
        test_task_classification_present_in_plan_mode,
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
            print(f"❌ {t.__name__} (unexpected): {e}")
            traceback.print_exc()
        print()
    print("=" * 50)
    if passed == len(tests):
        print(f"✅ All {passed} tests passed!")
    else:
        print(f"❌ {passed}/{len(tests)} tests passed")
        exit(1)
