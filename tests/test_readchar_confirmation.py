"""Test single-key confirmation with readchar."""

from unittest.mock import Mock, patch
import readchar


def test_single_key_confirmation_1():
    """Test pressing '1' for Yes."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='1'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                assert result is True, "Should return True for key '1'"
                assert cli.allow_all_tools is False, "Should not enable allow_all"
                print("✅ Single key '1' (Yes) works")


def test_single_key_confirmation_2():
    """Test pressing '2' for Yes to all."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='2'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                assert result is True, "Should return True for key '2'"
                assert cli.allow_all_tools is True, "Should enable allow_all"
                print("✅ Single key '2' (Yes to all) works")


def test_single_key_confirmation_3():
    """Test pressing '3' for No."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value='3'):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                assert result is False, "Should return False for key '3'"
                assert cli.allow_all_tools is False, "Should not enable allow_all"
                print("✅ Single key '3' (No) works")


def test_esc_key_cancels():
    """Test pressing Esc to cancel."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value=readchar.key.ESC):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                assert result is False, "Should return False for Esc key"
                print("✅ Esc key cancels correctly")


def test_ctrl_c_cancels():
    """Test pressing Ctrl+C to cancel."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            with patch('agentao.cli.transport.readchar.readkey', return_value=readchar.key.CTRL_C):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                assert result is False, "Should return False for Ctrl+C"
                print("✅ Ctrl+C cancels correctly")


def test_ignore_invalid_keys():
    """Test that invalid keys are ignored and prompt continues."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            # Simulate pressing 'a' (invalid), then '1' (valid)
            with patch('agentao.cli.transport.readchar.readkey', side_effect=['a', 'b', '1']):
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                assert result is True, "Should eventually accept '1' after ignoring invalid keys"
                print("✅ Invalid keys are ignored correctly")


def test_no_enter_required():
    """Test that pressing Enter is NOT required (single key input)."""

    with patch('agentao.cli.app.load_dotenv'), patch('agentao.cli.subcommands._load_and_register_plugins'):
        with patch('agentao.cli.app.Agentao') as mock_agent_class:
            # Mock readkey to return '1' - no Enter simulation needed
            with patch('agentao.cli.transport.readchar.readkey', return_value='1') as mock_readkey:
                from agentao.cli import AgentaoCLI

                cli = AgentaoCLI()

                result = cli.confirm_tool_execution(
                    "test_tool",
                    "Test description",
                    {"arg": "value"}
                )

                # readkey should be called exactly once (no Enter needed)
                assert mock_readkey.call_count == 1, "Should call readkey once (single key)"
                assert result is True
                print("✅ No Enter key required (true single-key input)")


if __name__ == "__main__":
    print("Testing single-key confirmation with readchar...")
    print()

    try:
        test_single_key_confirmation_1()
        print()
        test_single_key_confirmation_2()
        print()
        test_single_key_confirmation_3()
        print()
        test_esc_key_cancels()
        print()
        test_ctrl_c_cancels()
        print()
        test_ignore_invalid_keys()
        print()
        test_no_enter_required()
        print()
        print("=" * 50)
        print("✅ All tests passed!")
        print("\n[INFO] readchar provides TRUE single-key input")
        print("       - No Enter key required")
        print("       - Instant response on key press")
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
