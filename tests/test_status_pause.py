"""Test that Thinking spinner pauses during tool confirmation."""

from unittest.mock import Mock, patch, MagicMock


def test_status_paused_during_confirmation():
    """Test that status.stop() is called during confirmation."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar') as mock_readchar:
                # Mock the agent instance
                mock_agent = Mock()
                mock_agent_class.return_value = mock_agent

                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                # Create a mock status object
                mock_status = MagicMock()
                cli.current_status = mock_status

                # Mock user pressing "1" (Yes)
                mock_readchar.readkey.return_value = "1"

                # Call confirmation (should pause and resume status)
                result = cli.confirm_tool_execution(
                    tool_name="test_tool",
                    tool_description="Test description",
                    tool_args={"arg1": "value1"}
                )

                # Verify result
                assert result is True, "Should return True for option 1"

                # Verify status was stopped before menu
                mock_status.stop.assert_called_once()

                # Verify status was restarted after choice
                mock_status.start.assert_called_once()

                print("✅ Status is paused during confirmation")
                print("✅ Status is resumed after user choice")


def test_status_resumed_on_cancel():
    """Test that status is resumed even when user cancels."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar') as mock_readchar:
                mock_agent = Mock()
                mock_agent_class.return_value = mock_agent

                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()
                mock_status = MagicMock()
                cli.current_status = mock_status

                # Mock user pressing "3" (No/Cancel)
                mock_readchar.readkey.return_value = "3"

                result = cli.confirm_tool_execution(
                    tool_name="test_tool",
                    tool_description="Test description",
                    tool_args={}
                )

                assert result is False, "Should return False for option 3"
                mock_status.stop.assert_called_once()
                mock_status.start.assert_called_once()

                print("✅ Status is resumed even when cancelled")


def test_status_resumed_on_error():
    """Test that status is resumed even when readchar raises error."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar') as mock_readchar:
                mock_agent = Mock()
                mock_agent_class.return_value = mock_agent

                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()
                mock_status = MagicMock()
                cli.current_status = mock_status

                # Mock readchar raising an exception
                mock_readchar.readkey.side_effect = Exception("Test error")

                result = cli.confirm_tool_execution(
                    tool_name="test_tool",
                    tool_description="Test description",
                    tool_args={}
                )

                assert result is False, "Should return False on error"
                mock_status.stop.assert_called_once()
                mock_status.start.assert_called_once()

                print("✅ Status is resumed even on exception")


def test_status_not_paused_with_allow_all():
    """Test that status is NOT paused when allow_all mode is enabled."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            mock_agent = Mock()
            mock_agent_class.return_value = mock_agent

            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()
            cli.allow_all_tools = True  # Enable allow all mode

            mock_status = MagicMock()
            cli.current_status = mock_status

            # Call confirmation (should auto-approve without pausing)
            result = cli.confirm_tool_execution(
                tool_name="test_tool",
                tool_description="Test description",
                tool_args={}
            )

            assert result is True, "Should auto-approve with allow_all mode"

            # Status should NOT be stopped/started with allow_all
            mock_status.stop.assert_not_called()
            mock_status.start.assert_not_called()

            print("✅ Status is NOT paused with allow_all mode (no interruption)")


def test_status_none_doesnt_crash():
    """Test that confirmation works even if current_status is None."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar') as mock_readchar:
                mock_agent = Mock()
                mock_agent_class.return_value = mock_agent

                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()
                cli.current_status = None  # No active status

                mock_readchar.readkey.return_value = "1"

                # Should not crash when status is None
                result = cli.confirm_tool_execution(
                    tool_name="test_tool",
                    tool_description="Test description",
                    tool_args={}
                )

                assert result is True
                print("✅ Confirmation works even when status is None")


if __name__ == "__main__":
    print("Testing status pause during confirmation...\n")

    try:
        test_status_paused_during_confirmation()
        print()
        test_status_resumed_on_cancel()
        print()
        test_status_resumed_on_error()
        print()
        test_status_not_paused_with_allow_all()
        print()
        test_status_none_doesnt_crash()
        print()
        print("=" * 50)
        print("✅ All tests passed!")
        print("\n[INFO] 'Thinking...' spinner behavior:")
        print("       - Pauses when confirmation menu appears")
        print("       - Resumes after user makes a choice")
        print("       - Always resumes (even on error/cancel)")
        print("       - Not paused with 'allow all' mode")
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
