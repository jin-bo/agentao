"""Test menu-based tool confirmation feature."""

from unittest.mock import Mock, patch, call
import sys
from io import StringIO


def test_menu_confirmation_yes():
    """Test selecting 'Yes' (option 1) in confirmation menu."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='1'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                # Test confirmation with option 1 (Yes)
                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test tool description",
                    {"arg1": "value1"}
                )

                assert result is True, "Should return True for option 1"
                assert cli.allow_all_tools is False, "Should not enable allow_all mode"
                print("✅ Option 1 (Yes) works correctly")


def test_menu_confirmation_yes_to_all():
    """Test selecting 'Yes to all' (option 2) in confirmation menu."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='2'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                # Test confirmation with option 2 (Yes to all)
                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test tool description",
                    {"arg1": "value1"}
                )

                assert result is True, "Should return True for option 2"
                assert cli.allow_all_tools is True, "Should enable allow_all mode"
                print("✅ Option 2 (Yes to all) works correctly")


def test_menu_confirmation_no():
    """Test selecting 'No' (option 3) in confirmation menu."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='3'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                # Test confirmation with option 3 (No)
                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test tool description",
                    {"arg1": "value1"}
                )

                assert result is False, "Should return False for option 3"
                assert cli.allow_all_tools is False, "Should not enable allow_all mode"
                print("✅ Option 3 (No) works correctly")


def test_allow_all_mode_bypass():
    """Test that allow_all mode bypasses confirmation prompt."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()
            cli.allow_all_tools = True

            # Should not prompt when allow_all is enabled
            with patch('agentao.cli.transport.readchar.readkey') as mock_readkey:
                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test tool description",
                    {"arg1": "value1"}
                )

                assert result is True, "Should return True in allow_all mode"
                mock_readkey.assert_not_called()  # Should not prompt
                print("✅ Allow all mode bypasses confirmation")


def test_keyboard_interrupt_handling():
    """Test that Ctrl+C cancels confirmation."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', side_effect=KeyboardInterrupt):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                # Test Ctrl+C handling
                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test tool description",
                    {"arg1": "value1"}
                )

                assert result is False, "Should return False on Ctrl+C"
                print("✅ Keyboard interrupt (Ctrl+C) handled correctly")


def test_session_state_initialization():
    """Test that CLI initializes with allow_all_tools = False."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            from agentao.cli import AgentaoCLI

            cli = AgentaoCLI()

            assert hasattr(cli, 'allow_all_tools'), "Should have allow_all_tools attribute"
            assert cli.allow_all_tools is False, "Should initialize to False"
            print("✅ Session state initialized correctly")


def test_allow_all_persists_across_calls():
    """Test that allow_all mode persists across multiple tool calls."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='2'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                # First call - enable allow_all
                result1 = cli.confirm_tool_execution("tool1", "desc1", {})
                assert result1 is True
                assert cli.allow_all_tools is True

                # Second call - should auto-approve without prompting
                with patch('agentao.cli.transport.readchar.readkey') as mock_readkey:
                    result2 = cli.confirm_tool_execution("tool2", "desc2", {})
                    assert result2 is True
                    mock_readkey.assert_not_called()

                print("✅ Allow all mode persists across calls")


if __name__ == "__main__":
    print("Testing menu-based confirmation feature...")
    print()

    try:
        test_menu_confirmation_yes()
        print()
        test_menu_confirmation_yes_to_all()
        print()
        test_menu_confirmation_no()
        print()
        test_allow_all_mode_bypass()
        print()
        test_keyboard_interrupt_handling()
        print()
        test_session_state_initialization()
        print()
        test_allow_all_persists_across_calls()
        print()
        print("=" * 50)
        print("✅ All tests passed!")
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
