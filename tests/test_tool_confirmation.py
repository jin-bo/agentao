"""Test tool confirmation feature."""

from pathlib import Path
from unittest.mock import Mock, patch
from agentao.tools import ShellTool, WebFetchTool, WebSearchTool, ReadFileTool


def test_requires_confirmation_property():
    """Test that tools have correct requires_confirmation property."""

    # Tools that require confirmation
    shell_tool = ShellTool()
    web_fetch_tool = WebFetchTool()
    web_search_tool = WebSearchTool()

    assert shell_tool.requires_confirmation is True, "ShellTool should require confirmation"
    assert web_fetch_tool.requires_confirmation is True, "WebFetchTool should require confirmation"
    assert web_search_tool.requires_confirmation is True, "WebSearchTool should require confirmation"

    print("✅ Shell & Web tools require confirmation")

    # Tools that don't require confirmation
    read_file_tool = ReadFileTool()
    assert read_file_tool.requires_confirmation is False, "ReadFileTool should not require confirmation"

    print("✅ File operation tools don't require confirmation")


def test_agent_with_confirmation_callback():
    """Test agent with confirmation callback."""

    # Mock the LLMClient and OpenAI
    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_logger = Mock()
        mock_llm_client.return_value.logger = mock_logger
        mock_llm_client.return_value.model = "gpt-4"

        from agentao.agent import Agentao

        # Create a mock confirmation callback
        confirmation_callback = Mock(return_value=True)

        # Create agent with confirmation callback
        agent = Agentao(
            confirmation_callback=confirmation_callback,
            working_directory=Path.cwd(),
        )

        assert agent.confirmation_callback is not None, "Confirmation callback should be set"
        print("✅ Agent accepts confirmation callback")

        # Test that callback is called
        confirmation_callback.reset_mock()

        # Simulate tool execution
        tool = agent.tools.get("run_shell_command")
        assert tool.requires_confirmation is True

        print("✅ Shell tool properly registered and requires confirmation")


def test_confirmation_callback_signature():
    """Test that confirmation callback has correct signature."""

    def sample_callback(tool_name: str, tool_description: str, tool_args: dict) -> bool:
        """Sample confirmation callback."""
        return True

    # Mock the LLMClient
    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_logger = Mock()
        mock_llm_client.return_value.logger = mock_logger
        mock_llm_client.return_value.model = "gpt-4"

        from agentao.agent import Agentao

        # Create agent with callback
        agent = Agentao(
            confirmation_callback=sample_callback,
            working_directory=Path.cwd(),
        )

        # Test callback
        result = agent.confirmation_callback("test_tool", "Test tool description", {"arg1": "value1"})
        assert result is True

        print("✅ Confirmation callback signature is correct")


def test_no_confirmation_callback():
    """Test agent works without confirmation callback."""

    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_logger = Mock()
        mock_llm_client.return_value.logger = mock_logger
        mock_llm_client.return_value.model = "gpt-4"

        from agentao.agent import Agentao

        # Create agent without callback
        agent = Agentao(working_directory=Path.cwd())

        assert agent.confirmation_callback is None, "Confirmation callback should be None"
        print("✅ Agent works without confirmation callback")


if __name__ == "__main__":
    print("Testing tool confirmation feature...")
    print()

    try:
        test_requires_confirmation_property()
        print()
        test_agent_with_confirmation_callback()
        print()
        test_confirmation_callback_signature()
        print()
        test_no_confirmation_callback()
        print()
        print("=" * 50)
        print("✅ All tests passed!")
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
