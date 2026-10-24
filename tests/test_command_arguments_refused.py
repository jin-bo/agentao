"""``/clear``, ``/new`` and ``/compact`` refuse an argument (#574).

They take none, and used to drop any they were given: ``/clear just the
conversation`` still wiped every memory, ``/compact focus on X`` ran an
ordinary compaction. A non-blank argument now prints a notice and the
command does nothing else — no reset, no memory wipe, no compaction, no
lifecycle hook. A blank or whitespace-only argument runs as before.

The reset commands run against a real runtime rooted in ``tmp_path`` (HOME
redirected, as in ``test_clear_resets_confirm.py``), so a refused ``/clear``
is shown to leave a real stored memory in place.
"""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentao.cli.commands import (
    handle_clear_command,
    handle_compact_command,
    handle_new_command,
)
from agentao.embedding import build_from_environment
from agentao.memory.models import SaveMemoryRequest
from agentao.permissions import PermissionMode


@pytest.fixture
def cli(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows

    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI
        return AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path))


@pytest.fixture
def printed():
    lines = []
    with patch("agentao.cli._utils.console", SimpleNamespace(print=lines.append)):
        yield lines


def _state(cli):
    return (
        cli.current_session_id,
        list(cli.agent.messages),
        cli.allow_all_tools,
        cli.current_mode,
        [r.title for r in cli.agent.memory_manager.search("")],
    )


def _seed(cli):
    cli.agent.memory_manager.upsert(SaveMemoryRequest(key="kept", value="a stored fact"))
    cli.agent.messages.append({"role": "user", "content": "earlier"})
    cli.allow_all_tools = True  # undone by a real reset
    cli.on_session_end = Mock()
    cli.on_session_start = Mock()
    return _state(cli)


@pytest.mark.parametrize("handler, notice", [
    (handle_clear_command, "/clear takes no arguments; nothing was cleared"),
    (handle_new_command, "/new takes no arguments; the session was not reset"),
], ids=["clear", "new"])
@pytest.mark.parametrize("args", ["just the conversation", "  --all  ", "\tx", "all of it", "ALL"])
def test_a_reset_command_with_an_argument_changes_nothing(cli, printed, handler, notice, args):
    before = _seed(cli)
    with patch.object(cli.agent, "clear_history") as clear_history, \
            patch.object(cli.agent.memory_manager, "wipe_all") as wipe_all:
        handler(cli, args)

    assert _state(cli) == before
    clear_history.assert_not_called()
    wipe_all.assert_not_called()
    cli.on_session_end.assert_not_called()  # no SessionEnd / SessionStart hooks
    cli.on_session_start.assert_not_called()
    assert len(printed) == 1 and notice in printed[0]


def test_the_clear_notice_points_at_new(cli, printed):
    handle_clear_command(cli, "only history")
    assert "/new" in printed[0]


@pytest.mark.parametrize("args", ["", "   ", "\n", "all", "  all "])
def test_clear_with_a_blank_argument_still_clears(cli, printed, args):
    _seed(cli)
    with patch.object(cli.agent, "clear_history") as clear_history:
        handle_clear_command(cli, args)

    clear_history.assert_called_once()
    cli.on_session_end.assert_called_once_with(reason="clear")
    cli.on_session_start.assert_called_once_with(source="clear")
    assert cli.agent.memory_manager.search("") == []
    assert cli.allow_all_tools is False
    assert printed == []


@pytest.mark.parametrize("args", ["", "  "])
def test_new_with_a_blank_argument_still_resets_and_keeps_memories(cli, printed, args):
    _seed(cli)
    handle_new_command(cli, args)

    cli.on_session_end.assert_called_once_with(reason="clear")
    assert [r.title for r in cli.agent.memory_manager.search("")] == ["kept"]
    assert cli.allow_all_tools is False
    assert printed == []


def _compact_cli():
    messages = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    agent = SimpleNamespace(
        messages=messages,
        context_manager=Mock(),
        compaction_coordinator=Mock(),
        _build_system_prompt=Mock(return_value="system"),
    )
    return SimpleNamespace(agent=agent, _cached_ctx_pct=0.0, current_status=None), agent


@pytest.mark.parametrize("args", ["focus on the API changes", " x "])
def test_compact_with_an_argument_does_not_compact(printed, args):
    cli, agent = _compact_cli()
    before = list(agent.messages)

    handle_compact_command(cli, args)

    agent.compaction_coordinator.run.assert_not_called()  # so no PreCompact hook either
    agent._build_system_prompt.assert_not_called()
    assert agent.messages == before and cli.current_status is None
    assert len(printed) == 1 and "/compact takes no arguments; nothing was compacted" in printed[0]


def test_compact_with_a_blank_argument_still_compacts(printed):
    cli, agent = _compact_cli()
    agent.compaction_coordinator.run.side_effect = RuntimeError("reached")
    with patch("agentao.cli.commands.compact.console"), pytest.raises(RuntimeError, match="reached"):
        handle_compact_command(cli, "   ")
    agent.compaction_coordinator.run.assert_called_once()
    assert printed == []


def test_the_permission_mode_is_untouched_by_a_refusal(cli, printed):
    cli._apply_mode(PermissionMode.FULL_ACCESS)
    handle_clear_command(cli, "please")
    assert cli.current_mode == PermissionMode.FULL_ACCESS
