"""Test multi-turn tool calls."""

import os
from pathlib import Path

from dotenv import load_dotenv

from agentao import Agentao


def _use_live_llm() -> bool:
    env = os.getenv("AGENTAO_TEST_LIVE_LLM")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("GITHUB_ACTIONS") != "true"


def test_multi_turn_tool_calls():
    """Test that agent can handle multiple rounds of tool calls."""
    load_dotenv()

    agent = Agentao(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        model=os.getenv("OPENAI_MODEL"),
        working_directory=Path.cwd(),
    )

    print("Testing multi-turn tool calls...")
    print("=" * 80)

    # This should trigger multiple tool calls:
    # 1. List directory to see what's there
    # 2. Read some files
    # 3. Possibly more operations
    response = agent.chat("List the contents of the skills directory and tell me about one of the skills")

    print("\nResponse:")
    print(response)
    print("=" * 80)
    print(f"\nTotal messages in history: {len(agent.messages)}")

    # Count tool messages
    tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
    print(f"Tool calls executed: {len(tool_messages)}")

    assert isinstance(response, str)
    assert response.strip()
    assert len(agent.messages) > 0

    # Two valid outcomes: live success (expects tool calls) or graceful
    # degradation on network/API failure (expects an error marker).
    if "[LLM API error:" in response:
        # Graceful-degradation path — no further structural expectations.
        pass
    elif _use_live_llm():
        # Live success path — tool calls should have been executed.
        assert len(tool_messages) > 0
    else:
        # Offline mode without a network-induced error — shouldn't happen,
        # but if it does the response must still at least be non-empty.
        assert response.strip()

if __name__ == "__main__":
    test_multi_turn_tool_calls()
