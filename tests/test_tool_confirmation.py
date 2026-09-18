"""Test tool confirmation feature."""

from pathlib import Path
from unittest.mock import Mock, patch
import pytest

from agentao.tools import ShellTool, WebFetchTool, WebSearchTool, ReadFileTool

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


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


def _agent(**kwargs):
    with patch('agentao.agent.LLMClient') as mock_llm_client:
        mock_llm_client.return_value.logger = Mock()
        mock_llm_client.return_value.model = "gpt-4"

        from agentao.agent import Agentao

        return Agentao(working_directory=Path.cwd(), **kwargs)


def test_a_confirmation_callback_reaches_the_agent_through_a_transport():
    """The legacy callback survives 0.5.0 on ``build_compat_transport``.

    ``Agentao(confirmation_callback=...)`` is gone; the callback itself is
    not. Wrapped, it is what ``agent.transport.confirm_tool`` asks — with the
    same three arguments, and its answer is the answer.
    """
    from agentao.embedding.compat import build_compat_transport

    confirmation_callback = Mock(return_value=False)
    agent = _agent(
        transport=build_compat_transport(confirmation_callback=confirmation_callback),
    )

    tool = agent.tools.get("run_shell_command")
    assert tool.requires_confirmation is True

    args = {"command": "ls"}
    assert agent.transport.confirm_tool("run_shell_command", "List files", args) is False
    confirmation_callback.assert_called_once_with("run_shell_command", "List files", args)


def test_without_a_transport_every_confirmation_is_approved():
    """The headless default, unchanged: ``NullTransport`` says yes."""
    from agentao.transport import NullTransport

    agent = _agent()

    assert isinstance(agent.transport, NullTransport)
    assert agent.transport.confirm_tool("run_shell_command", "List files", {}) is True


if __name__ == "__main__":
    print("Testing tool confirmation feature...")
    print()

    try:
        test_requires_confirmation_property()
        print()
        test_a_confirmation_callback_reaches_the_agent_through_a_transport()
        print()
        test_without_a_transport_every_confirmation_is_approved()
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
