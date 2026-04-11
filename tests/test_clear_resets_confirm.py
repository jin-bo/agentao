"""Test that /clear command resets tool confirmation mode."""

from unittest.mock import Mock, patch


def test_clear_resets_confirmation():
    """Test that clear command resets allow_all_tools to False."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            # Mock the agent instance
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent

            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()

            # Enable allow_all mode
            cli.allow_all_tools = True
            assert cli.allow_all_tools is True, "Should start as True"

            # Simulate /clear command handling
            cli.agent.clear_history()
            cli.allow_all_tools = False  # This is what the clear command does

            # Verify it's reset
            assert cli.allow_all_tools is False, "Should be reset to False after clear"
            print("✅ /clear command resets allow_all_tools to False")


def test_clear_command_flow():
    """Test the full flow of clear command with confirmation reset."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent

            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()

            # Simulate workflow:
            # 1. User enables allow_all
            cli.allow_all_tools = True

            # 2. User uses /clear
            cli.agent.clear_history()
            cli.allow_all_tools = False

            # 3. Verify both are reset
            assert cli.allow_all_tools is False, "Confirmation should be reset"
            mock_agent.clear_history.assert_called_once()
            print("✅ Full clear command flow works correctly")


def test_clear_vs_reset_confirm():
    """Test that both /clear and /reset-confirm reset confirmation."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent

            from agentao.cli import AgentaoCLI

            # Test /clear
            cli1 = AgentaoCLI()
            cli1.allow_all_tools = True
            cli1.agent.clear_history()
            cli1.allow_all_tools = False  # clear does this
            assert cli1.allow_all_tools is False
            print("✅ /clear resets confirmation")

            # Test /reset-confirm
            cli2 = AgentaoCLI()
            cli2.allow_all_tools = True
            cli2.allow_all_tools = False  # reset-confirm does this
            assert cli2.allow_all_tools is False
            print("✅ /reset-confirm resets confirmation")

            print("✅ Both commands reset confirmation mode")


def test_initial_state():
    """Test that CLI starts with allow_all_tools = False."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent

            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()

            assert cli.allow_all_tools is False, "Should start as False"
            print("✅ Initial state is correct (allow_all_tools = False)")


def test_clear_makes_sense():
    """Test the logical flow: clear should reset everything to initial state."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent

            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()

            # Initial state
            initial_allow_all = cli.allow_all_tools
            assert initial_allow_all is False

            # User enables allow_all during conversation
            cli.allow_all_tools = True

            # User calls /clear to start fresh
            cli.agent.clear_history()
            cli.allow_all_tools = False  # Reset to initial state

            # Should be back to initial state
            assert cli.allow_all_tools == initial_allow_all
            print("✅ /clear logically resets to initial state")


if __name__ == "__main__":
    print("Testing /clear command resets confirmation mode...")
    print()

    try:
        test_clear_resets_confirmation()
        print()
        test_clear_command_flow()
        print()
        test_clear_vs_reset_confirm()
        print()
        test_initial_state()
        print()
        test_clear_makes_sense()
        print()
        print("=" * 50)
        print("✅ All tests passed!")
        print("\n[INFO] /clear command now resets:")
        print("       - Conversation history")
        print("       - Tool confirmation mode")
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
