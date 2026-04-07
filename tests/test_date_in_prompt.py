"""Test that current date is injected into user messages as <system-reminder>."""

import re
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock


def _make_agent():
    with patch('agentao.agent.LLMClient') as mock_llm_class:
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-4"
        mock_llm_class.return_value = mock_llm
        from agentao.agent import Agentao
        return Agentao()


def test_date_not_in_system_prompt():
    """Date should no longer be in the system prompt (moved to user message)."""
    agent = _make_agent()
    system_prompt = agent._build_system_prompt()

    assert "Current Date and Time:" not in system_prompt, (
        "Date should NOT be in system prompt — it is now injected as <system-reminder> in user messages"
    )
    print("✅ Date correctly absent from system prompt")


def test_date_injected_into_user_message():
    """chat() should prepend <system-reminder> with date/time to the user message."""
    agent = _make_agent()

    # Stub LLM to return a canned response without tool calls
    fake_response = MagicMock()
    fake_response.choices[0].message.tool_calls = None
    fake_response.choices[0].message.content = "Hello!"
    fake_response.choices[0].message.reasoning_content = None
    agent._llm_call = Mock(return_value=fake_response)

    agent.chat("Say hello")

    # The first (and only) user message in history should contain system-reminder + date
    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert user_msgs, "Should have at least one user message"
    content = user_msgs[0]["content"]

    assert "<system-reminder>" in content, "User message should contain <system-reminder>"
    assert "Current Date/Time:" in content, "User message should contain 'Current Date/Time:'"
    assert "Say hello" in content, "User message should still contain the original text"

    # Verify date format
    date_pattern = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'
    assert re.search(date_pattern, content), "Date should be in YYYY-MM-DD HH:MM:SS format"

    now = datetime.now()
    assert now.strftime("%A") in content, "Day of week should be present"
    print("✅ Date correctly injected as <system-reminder> in user message")


def test_date_with_project_instructions():
    """Date is absent from system prompt even when project instructions are loaded."""
    agent = _make_agent()
    agent.project_instructions = "Use uv for packages."

    system_prompt = agent._build_system_prompt()

    assert "Current Date and Time:" not in system_prompt, (
        "Date should NOT be in system prompt even with project instructions"
    )
    assert "=== Project Instructions ===" in system_prompt
    print("✅ Date absent from system prompt with project instructions")


if __name__ == "__main__":
    print("Testing date injection behaviour...")
    print()

    tests = [
        test_date_not_in_system_prompt,
        test_date_injected_into_user_message,
        test_date_with_project_instructions,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print()
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
        except Exception as e:
            import traceback
            print(f"❌ {t.__name__}: unexpected error")
            traceback.print_exc()

    print("=" * 50)
    print(f"{'✅ All' if passed == len(tests) else f'❌ {passed}/{len(tests)}'} tests passed!")
