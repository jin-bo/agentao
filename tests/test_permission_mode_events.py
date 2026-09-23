"""A permission-mode switch is recorded, whichever path made it.

Read-only has two switches — the engine's preset and
``ToolRunner.readonly_mode`` — and neither the engine nor
``PermissionEngine.set_mode`` can emit: the engine holds no transport by
design. So every entry path used to move the switches by hand, and ACP
``session/set_mode`` moved only one of them and emitted nothing: a replay
of an ACP session showed the read-only denials with no record of when the
session became read-only.

``runtime/permission_mode.py::apply_permission_mode`` is now the one place
that does it, and these tests drive the real entry points — the real ACP
handler, the real ``AgentaoCLI``, the public ``Agentao.set_permission_mode``
— against a real ``PermissionEngine`` and a real ``ToolRunner``.
"""

from __future__ import annotations

import logging
from functools import partial
from unittest.mock import patch

import pytest

from agentao import Agentao
from agentao.acp import session_set_mode as acp_set_mode
from agentao.acp.models import AcpSessionState
from agentao.permissions import PermissionEngine, PermissionMode
from agentao.transport import EventType

from .support.acp_server import make_initialized_server


@pytest.fixture
def agent(tmp_path):
    agent = Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path,
        logger=logging.getLogger("test.permission_mode_events"),
        permission_engine=PermissionEngine(project_root=tmp_path),
    )
    yield agent
    agent.close()


def _record(agent):
    """Spy on the agent's transport, returning the captured list.

    Records on the transport *object* rather than through ``subscribe``,
    because ``ToolRunner`` holds its own reference to the same object and
    ``AgentaoCLI`` — which is its own transport — has no ``subscribe`` at
    all. The real ``emit`` still runs, so nothing downstream is stubbed out.
    """
    events = []
    transport = agent.transport
    real_emit = transport.emit

    def _spy(event):
        events.append(event)
        return real_emit(event)

    transport.emit = _spy
    return events


def _modes(events):
    return [
        (e.data.get("previous"), e.data.get("current"), e.data.get("cause"))
        for e in events
        if e.type is EventType.PERMISSION_MODE_CHANGED
    ]


def _kinds(events):
    return [e.type for e in events]


def _acp(agent):
    """Register ``agent`` as ACP session ``s`` and return the server."""
    server = make_initialized_server()
    state = AcpSessionState(session_id="s")
    state.agent = agent
    server.sessions.create(state)
    return server


def _set_mode(server, mode_id):
    return acp_set_mode.handle_session_set_mode(
        server, {"sessionId": "s", "modeId": mode_id},
    )


# ---------------------------------------------------------------------------
# ACP — the path that recorded nothing
# ---------------------------------------------------------------------------


def test_acp_set_mode_records_the_transition(agent):
    server = _acp(agent)
    events = _record(agent)

    _set_mode(server, "read-only")

    # Both switches moved, so both events fire — in the order the CLI has
    # always emitted them.
    assert _kinds(events) == [
        EventType.READONLY_MODE_CHANGED,
        EventType.PERMISSION_MODE_CHANGED,
    ]
    assert events[0].data == {"previous": False, "current": True}
    assert _modes(events) == [("workspace-write", "read-only", "acp")]


def test_acp_set_mode_moves_the_runner_flag_too(agent):
    server = _acp(agent)

    _set_mode(server, "read-only")
    assert agent.tool_runner.readonly_mode is True

    _set_mode(server, "workspace-write")
    # Clearing only the engine's mode would leave the flag set, and
    # ``readonly_active`` honours either — an ACP client could enter
    # read-only and never be able to leave it.
    assert agent.tool_runner.readonly_mode is False
    assert agent.tool_runner.readonly_active() is False


def test_a_repeated_acp_set_mode_records_nothing(agent):
    server = _acp(agent)
    _set_mode(server, "read-only")
    events = _record(agent)

    _set_mode(server, "read-only")

    # The client still gets its ``current_mode_update`` — that notification
    # answers the request. The replay timeline gets nothing, because the
    # posture did not move.
    assert events == []


def test_a_non_preset_mode_id_records_no_transition(agent):
    server = _acp(agent)
    events = _record(agent)

    assert _set_mode(server, "code") == {"modeId": "code"}

    assert events == []
    assert agent.permission_engine.active_mode is PermissionMode.WORKSPACE_WRITE


# ---------------------------------------------------------------------------
# CLI and the host API
# ---------------------------------------------------------------------------


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A real ``AgentaoCLI`` on a runtime rooted in ``tmp_path``.

    HOME is redirected because ``AgentaoCLI`` construction opens the
    user-scope memory store under ``~/.agentao``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows

    from agentao.embedding import build_from_environment

    with patch("agentao.cli.app.safe_load_dotenv"), \
            patch("agentao.cli.subcommands._load_and_register_plugins"):
        from agentao.cli import AgentaoCLI
        return AgentaoCLI(agent_factory=partial(
            build_from_environment, working_directory=tmp_path,
        ))


def test_the_cli_still_labels_its_own_switch(cli):
    events = _record(cli.agent)

    cli._apply_mode(PermissionMode.READ_ONLY)

    assert _modes(events) == [("workspace-write", "read-only", "cli")]
    # The CLI's own mirror of the flag, which the status toolbar reads,
    # stays in step with the runner's.
    assert cli.readonly_mode is True
    assert cli.agent.tool_runner.readonly_mode is True


def test_the_host_api_switches_both_and_names_itself(agent):
    events = _record(agent)

    previous = agent.set_permission_mode(PermissionMode.READ_ONLY)

    assert previous is PermissionMode.WORKSPACE_WRITE
    assert agent.permission_engine.active_mode is PermissionMode.READ_ONLY
    assert agent.tool_runner.readonly_mode is True
    assert _modes(events) == [("workspace-write", "read-only", "host")]


def test_a_runtime_with_no_engine_refuses_rather_than_half_applying(tmp_path):
    agent = Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path,
        logger=logging.getLogger("test.permission_mode_events"),
    )
    try:
        assert agent.permission_engine is None
        with pytest.raises(ValueError, match="no permission engine"):
            agent.set_permission_mode(PermissionMode.READ_ONLY)
        # Nothing was applied — in particular the runner's flag was not set
        # on a runtime that has nothing to enforce the rest of the posture.
        assert agent.tool_runner.readonly_mode is False
    finally:
        agent.close()


def test_answering_yes_to_all_records_the_escalation(cli):
    """The confirmation prompt's "2" escalates to full-access.

    It deliberately does not go through ``_apply_mode`` — that would reset
    ``allow_all_tools`` and persist the grant to ``settings.json``, and this
    one is for the session only — so it used to set the engine's mode and
    the flag by hand and record nothing. A replay then showed the session
    running at full access with no event saying it had been granted.
    """
    events = _record(cli.agent)

    with patch("agentao.cli.transport.readchar.readkey", return_value="2"):
        assert cli.confirm_tool_execution("run_shell_command", "", {}) is True

    assert cli.allow_all_tools is True
    assert cli.current_mode is PermissionMode.FULL_ACCESS
    assert cli.agent.permission_engine.active_mode is PermissionMode.FULL_ACCESS
    assert _modes(events) == [
        ("workspace-write", "full-access", "cli-allow-all"),
    ]
