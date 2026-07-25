"""Test that /clear command resets tool confirmation mode.

These tests drive the production reset path rather than simulating it. `/clear`
(``agentao/cli/commands/reset.py::handle_clear_command``) resets blanket tool
auto-approval indirectly, by calling
``cli._apply_mode(PermissionMode.WORKSPACE_WRITE)`` — which is where
``allow_all_tools = False`` actually lives (``agentao/cli/app.py::_apply_mode``).
Asserting a hand-assigned ``cli.allow_all_tools = False`` would pass even if
that line were deleted, leaving a user who answered "yes to all" with blanket
auto-approval across `/clear`.

Until the dispatch-table refactor, `/clear` lived inline in ``run_loop`` and
was unreachable from a test, so ``_clear_reset`` below re-implemented the one
line it cared about. It now calls the real handler, so a reordering or removal
inside the production reset sequence is caught here.

The runtime is injected via ``AgentaoCLI(agent_factory=...)`` and pinned to a
``tmp_path``. Patching ``agentao.cli.app.build_from_environment`` was never a
supported seam (``docs/design/cli-host-agent-factory.md`` §1), and a bare
``Mock()`` runtime is now rejected by the §3.1 post-conditions.
"""

from functools import partial
from unittest.mock import patch

import pytest

from agentao.embedding import build_from_environment
from agentao.permissions import PermissionMode


@pytest.fixture
def cli(tmp_path):
    """An AgentaoCLI backed by a real runtime rooted in ``tmp_path``."""
    with patch('agentao.cli.app.safe_load_dotenv'), \
            patch('agentao.cli.subcommands._load_and_register_plugins'):
        from agentao.cli import AgentaoCLI
        return AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path))


def _clear_reset(cli):
    """Run the real `/clear` handler."""
    from agentao.cli.commands import handle_clear_command

    handle_clear_command(cli, "")


def test_clear_resets_confirmation(cli):
    """/clear turns blanket auto-approval back off."""
    cli.allow_all_tools = True

    _clear_reset(cli)

    assert cli.allow_all_tools is False, "Should be reset to False after clear"


def test_clear_command_flow(cli):
    """History clearing and confirmation reset both happen.

    ``clear_history`` is asserted on the *handler's* call, not a manual one
    the test makes itself — that is what makes this a regression test.
    """
    cli.allow_all_tools = True

    with patch.object(cli.agent, 'clear_history') as mock_clear:
        _clear_reset(cli)

    assert cli.allow_all_tools is False, "Confirmation should be reset"
    mock_clear.assert_called_once()


def test_clear_resets_from_every_mode(cli):
    """The reset holds regardless of the mode the user was in."""
    for mode in (PermissionMode.READ_ONLY, PermissionMode.FULL_ACCESS,
                 PermissionMode.WORKSPACE_WRITE):
        cli._apply_mode(mode)
        cli.allow_all_tools = True

        _clear_reset(cli)

        assert cli.allow_all_tools is False, f"not reset when coming from {mode}"


def test_clear_restores_workspace_write(cli):
    """/clear also drops an escalated posture back to workspace-write."""
    cli._apply_mode(PermissionMode.FULL_ACCESS)
    assert cli.current_mode == PermissionMode.FULL_ACCESS

    _clear_reset(cli)

    assert cli.current_mode == PermissionMode.WORKSPACE_WRITE
    assert cli.permission_engine.active_mode == PermissionMode.WORKSPACE_WRITE


def test_initial_state(cli):
    """CLI starts with allow_all_tools = False."""
    assert cli.allow_all_tools is False, "Should start as False"


def test_clear_makes_sense(cli):
    """The logical flow: clear returns everything to the initial state."""
    initial_allow_all = cli.allow_all_tools
    assert initial_allow_all is False

    cli.allow_all_tools = True
    _clear_reset(cli)

    assert cli.allow_all_tools == initial_allow_all
