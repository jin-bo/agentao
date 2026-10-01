"""ACP client Terminal Auth (#381): capabilities, auth_required, login, restart.

Runs against :mod:`tests.support.acp_auth_agent`, a real subprocess agent that
reads its credential only at process start — so every "the login worked"
assertion here also proves the client restarted the process, not merely
opened a new session on the old one.
"""

from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace

import pytest

from agentao.acp_client.auth import (
    AUTH_REQUIRED_RPC_CODE,
    AuthMethodError,
    build_terminal_login_command,
    is_auth_required,
    terminal_auth_methods,
)
from agentao.acp_client.client import AcpClientError, AcpErrorCode, AcpRpcError
from agentao.acp_client.manager import ACPManager
from agentao.acp_client.models import AcpClientConfig, AcpServerConfig, ServerState
from agentao.cli.commands_ext import acp_login as login_mod
from tests.support.acp_auth_agent import AuthAgent

NAME = "mock"
TIMEOUT = 20.0


@pytest.fixture
def agent(tmp_path):
    return AuthAgent(tmp_path)


def _manager(agent, *, terminal_auth, **extra):
    config = AcpClientConfig.from_dict(
        {"servers": {NAME: agent.server_raw(**extra)}}, project_root=agent.dir,
    )
    return ACPManager(config, terminal_auth=terminal_auth)


@pytest.fixture
def managers():
    made = []
    yield made
    for mgr in made:
        mgr.stop_all()


def _new(managers, agent, *, terminal_auth=True, **extra):
    mgr = _manager(agent, terminal_auth=terminal_auth, **extra)
    managers.append(mgr)
    return mgr


def _login_with(agent, mgr, token):
    """Run the advertised terminal login non-interactively, feeding *token*."""
    method = terminal_auth_methods(mgr.auth_methods(NAME))[0]
    command = build_terminal_login_command(mgr.get_handle(NAME).config, method)
    done = subprocess.run(
        command.argv, input=token + "\n", text=True, env=command.env,
        cwd=command.cwd, timeout=TIMEOUT,
    )
    return done.returncode


def _server_pids(agent):
    return [e["pid"] for e in agent.entries("server")]


# ---------------------------------------------------------------------------
# Capability declaration + authMethods retention
# ---------------------------------------------------------------------------


def test_headless_manager_declares_no_terminal_auth(managers, agent):
    mgr = _new(managers, agent, terminal_auth=False)

    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)

    assert agent.entries("initialize")[-1]["clientCapabilities"] == {}
    assert [m["id"] for m in mgr.auth_methods(NAME)] == ["oauth"]


def test_terminal_capable_manager_declares_both_spellings(managers, agent):
    mgr = _new(managers, agent, terminal_auth=True)

    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)

    caps = agent.entries("initialize")[-1]["clientCapabilities"]
    assert caps["auth"] == {"terminal": True}
    assert caps["_meta"] == {"terminal-auth": True}
    # Retained although session/new failed and the handshake was torn down.
    assert [m["id"] for m in mgr.auth_methods(NAME)] == ["mock-login", "oauth"]


# ---------------------------------------------------------------------------
# auth_required is not a handshake failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prestart", [False, True], ids=["cold", "prewarmed"])
def test_repeated_auth_required_never_turns_the_server_fatal(managers, agent, prestart):
    mgr = _new(managers, agent)
    if prestart:
        mgr.start_server(NAME)

    for _ in range(3):
        with pytest.raises(AcpRpcError) as info:
            mgr.connect_server(NAME, timeout=TIMEOUT)
        exc = info.value
        assert is_auth_required(exc)
        assert exc.rpc_code == AUTH_REQUIRED_RPC_CODE  # numeric contract kept
        assert exc.details["auth_required"] is True
        assert [m["id"] for m in exc.details["auth_methods"]] == ["mock-login", "oauth"]

    assert not mgr.is_fatal(NAME)
    assert mgr._handshake_fail_streak[NAME] == 0


def test_repeated_auth_required_from_send_prompt_stays_auth_required(managers, agent):
    mgr = _new(managers, agent)

    for _ in range(3):
        with pytest.raises(AcpRpcError) as info:
            mgr.send_prompt(NAME, "hi", timeout=TIMEOUT)
        assert is_auth_required(info.value)

    assert not mgr.is_fatal(NAME)


# ---------------------------------------------------------------------------
# The login process
# ---------------------------------------------------------------------------


def _config(agent, **env):
    raw = agent.server_raw()
    raw["env"].update(env)
    return AcpServerConfig.from_dict(NAME, raw, project_root=agent.dir)


def test_login_command_appends_args_and_overrides_env(agent, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    config = _config(agent)
    method = {
        "id": "m", "type": "terminal", "args": ["--login", "--quiet"],
        "env": {"MOCK_LOGIN": "1", "MOCK_BASE": "from-method"},
    }

    command = build_terminal_login_command(config, method)

    assert command.argv[1:] == [str(agent.script), "--acp", "--login", "--quiet"]
    assert command.cwd == config.cwd
    assert command.env["MOCK_LOGIN"] == "1"
    assert command.env["MOCK_BASE"] == "from-method"       # method env wins
    assert command.env["MOCK_AUTH_FILE"] == str(agent.cred_file)  # base kept
    assert "OPENAI_API_KEY" not in command.env             # same scrub as the server


def test_login_command_keeps_an_explicitly_configured_provider_key(agent, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    config = _config(agent, OPENAI_API_KEY="sk-configured")

    command = build_terminal_login_command(config, {"id": "m", "type": "terminal"})

    assert command.env["OPENAI_API_KEY"] == "sk-configured"


@pytest.mark.parametrize(
    "method",
    [
        {"id": "oauth"},                                   # agent (default type)
        {"id": "e", "type": "env_var"},
        {"id": "m", "type": "terminal", "args": "--login"},
        {"id": "m", "type": "terminal", "env": {"A": 1}},
        {"type": "terminal"},
    ],
)
def test_unsupported_or_malformed_methods_are_refused(agent, method):
    with pytest.raises(AuthMethodError):
        build_terminal_login_command(_config(agent), method)


def test_a_login_on_the_same_process_is_not_enough(managers, agent):
    """The fake is start-time-only: a new session without a restart fails."""
    mgr = _new(managers, agent)
    mgr.start_server(NAME)
    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)

    assert _login_with(agent, mgr, "tok") == 0
    with pytest.raises(AcpRpcError) as info:
        mgr.connect_server(NAME, timeout=TIMEOUT)

    assert is_auth_required(info.value)


# ---------------------------------------------------------------------------
# /acp login
# ---------------------------------------------------------------------------


def _cli(mgr):
    return SimpleNamespace(_acp_manager=mgr, markdown_mode=False)


@pytest.fixture
def piped_login(monkeypatch):
    """Replace the TTY runner with one that feeds a scripted answer."""
    calls = []

    def run(command, answer="tok"):
        calls.append(command)
        if answer is None:
            return login_mod.LoginOutcome("cancelled", returncode=-2)
        done = subprocess.run(
            command.argv, input=answer + "\n", text=True, env=command.env,
            cwd=command.cwd, timeout=TIMEOUT,
        )
        return login_mod.LoginOutcome(
            "ok" if done.returncode == 0 else "failed", returncode=done.returncode,
        )

    state = {"answer": "tok"}
    monkeypatch.setattr(login_mod, "run_terminal_login", lambda c: run(c, state["answer"]))
    return SimpleNamespace(calls=calls, state=state)


def test_acp_login_runs_the_method_restarts_and_connects(managers, agent, piped_login, capsys):
    mgr = _new(managers, agent)
    mgr.start_server(NAME)
    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)
    before = _server_pids(agent)

    login_mod.acp_login(_cli(mgr), NAME)

    out = capsys.readouterr().out
    assert "Logged in to 'mock' and reconnected" in out
    login = agent.entries("login")[-1]
    assert login["argv"] == ["--acp", "--login"]  # base args, then the method's
    assert login["cwd"] == str(agent.cwd.resolve())
    assert login["env"]["MOCK_LOGIN"] == "1" and login["env"]["MOCK_BASE"] == "base"
    after = _server_pids(agent)
    assert len(after) == len(before) + 1 and after[-1] not in before  # restarted
    assert mgr.get_client(NAME).connection_info.session_id == "s-tok"


def test_acp_login_learns_methods_with_a_first_connect(managers, agent, piped_login, capsys):
    mgr = _new(managers, agent)

    login_mod.acp_login(_cli(mgr), NAME)

    assert "reconnected" in capsys.readouterr().out
    assert mgr.get_client(NAME).connection_info.session_id == "s-tok"


@pytest.mark.parametrize(
    "answer, message",
    [("fail", "failed (exit status 3)"), ("", "failed (exit status 3)"), (None, "cancelled")],
)
def test_a_failed_or_cancelled_login_does_not_restart(
    managers, agent, piped_login, capsys, answer, message,
):
    mgr = _new(managers, agent)
    mgr.start_server(NAME)
    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)
    before = _server_pids(agent)
    piped_login.state["answer"] = answer

    login_mod.acp_login(_cli(mgr), NAME)

    assert message in capsys.readouterr().out
    assert _server_pids(agent) == before
    assert not agent.cred_file.exists()


def test_a_login_that_launches_nothing_is_a_failure(managers, agent, capsys):
    mgr = _new(managers, agent, command=str(agent.dir / "missing-runner"))

    login_mod.acp_login(_cli(mgr), NAME)  # the first connect cannot start it

    assert "Could not reach 'mock'" in capsys.readouterr().out


def test_still_auth_required_after_login_reports_once_without_looping(
    managers, agent, monkeypatch, capsys,
):
    mgr = _new(managers, agent)
    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)
    calls = []
    # "Succeeds" without writing a credential.
    monkeypatch.setattr(
        login_mod, "run_terminal_login",
        lambda c: calls.append(c) or login_mod.LoginOutcome("ok", returncode=0),
    )

    login_mod.acp_login(_cli(mgr), NAME)

    assert "still requires authentication" in capsys.readouterr().out
    assert len(calls) == 1
    assert not mgr.is_fatal(NAME)


def test_acp_login_refuses_without_a_terminal(managers, agent, piped_login, capsys):
    mgr = _new(managers, agent, terminal_auth=False)

    login_mod.acp_login(_cli(mgr), NAME)

    assert "needs an interactive terminal" in capsys.readouterr().out
    assert piped_login.calls == []


def test_only_agent_methods_point_to_external_authentication(managers, agent, piped_login, capsys):
    mgr = _new(managers, agent)
    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)

    login_mod.acp_login(_cli(mgr), f"{NAME} oauth")

    out = capsys.readouterr().out
    assert "only terminal methods" in out
    assert piped_login.calls == []


def test_auth_required_hint_names_the_login_command(managers, agent):
    mgr = _new(managers, agent)
    with pytest.raises(AcpRpcError) as info:
        mgr.connect_server(NAME, timeout=TIMEOUT)

    assert f"/acp login {NAME}" in login_mod.auth_required_hint(mgr, NAME, info.value)

    headless = _new(managers, agent, terminal_auth=False)
    with pytest.raises(AcpRpcError) as info:
        headless.connect_server(NAME, timeout=TIMEOUT)
    hint = login_mod.auth_required_hint(headless, NAME, info.value)
    assert "authenticate outside Agentao" in hint and "agent" in hint


# ---------------------------------------------------------------------------
# Login reservation (SERVER_BUSY)
# ---------------------------------------------------------------------------


def _hold_turn(mgr):
    lock = mgr._get_server_lock(NAME)
    assert lock.acquire(blocking=False)
    return lock


def test_login_during_an_active_turn_is_server_busy(managers, agent, piped_login, monkeypatch, capsys):
    mgr = _new(managers, agent)
    with pytest.raises(AcpRpcError):
        mgr.connect_server(NAME, timeout=TIMEOUT)
    restarts = []
    monkeypatch.setattr(mgr, "restart_server", lambda n: restarts.append(n))
    lock = _hold_turn(mgr)
    try:
        login_mod.acp_login(_cli(mgr), NAME)
    finally:
        lock.release()

    assert "has an active turn" in capsys.readouterr().out
    assert piped_login.calls == [] and restarts == []


def test_reservation_keeps_other_threads_off_the_server(managers, agent):
    mgr = _new(managers, agent)
    started = len(_server_pids(agent))
    errors = {}

    def attempt(label, fn):
        try:
            fn()
        except AcpClientError as exc:
            errors[label] = exc

    with mgr.reserve_for_login(NAME):
        threads = [
            threading.Thread(target=attempt, args=(label, fn))
            for label, fn in [
                ("send", lambda: mgr.send_prompt(NAME, "x", timeout=TIMEOUT)),
                ("nonblocking", lambda: mgr.send_prompt_nonblocking(NAME, "x", timeout=TIMEOUT)),
                ("once", lambda: mgr.prompt_once(NAME, "x", timeout=TIMEOUT)),
                ("connect", lambda: mgr.connect_server(NAME, timeout=TIMEOUT)),
                ("ensure", lambda: mgr.ensure_connected(NAME, timeout=TIMEOUT)),
            ]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(TIMEOUT)
        with pytest.raises(AcpClientError) as nested:
            with mgr.reserve_for_login(NAME):
                pass

    assert set(errors) == {"send", "nonblocking", "once", "connect", "ensure"}
    assert all(e.code is AcpErrorCode.SERVER_BUSY for e in errors.values())
    assert nested.value.code is AcpErrorCode.SERVER_BUSY
    assert len(_server_pids(agent)) == started  # nothing launched meanwhile


def test_reservation_leaves_other_servers_alone(tmp_path):
    first, second = AuthAgent(tmp_path / "a"), AuthAgent(tmp_path / "b")
    second.cred_file.write_text("tok", encoding="utf-8")
    config = AcpClientConfig.from_dict(
        {"servers": {"a": first.server_raw(), "b": second.server_raw()}}, project_root=tmp_path,
    )
    mgr = ACPManager(config, terminal_auth=True)
    try:
        with mgr.reserve_for_login("a"):
            result = mgr.send_prompt("b", "hi", timeout=TIMEOUT)
        assert result["stopReason"] == "end_turn"
    finally:
        mgr.stop_all()


# ---------------------------------------------------------------------------
# add_server
# ---------------------------------------------------------------------------


def test_add_server_registers_a_stopped_usable_server(tmp_path):
    running, added = AuthAgent(tmp_path / "a"), AuthAgent(tmp_path / "b")
    running.cred_file.write_text("one", encoding="utf-8")
    added.cred_file.write_text("two", encoding="utf-8")
    mgr = ACPManager(AcpClientConfig.from_dict(
        {"servers": {"a": running.server_raw()}}, project_root=tmp_path,
    ))
    try:
        mgr.send_prompt("a", "hi", timeout=TIMEOUT)
        pid = mgr.get_handle("a").info.pid

        mgr.add_server("b", AcpServerConfig.from_dict("b", added.server_raw(), tmp_path))

        assert mgr.get_handle("b").info.state is ServerState.CONFIGURED
        assert added.entries("server") == []                 # not started
        assert mgr.get_handle("a").info.pid == pid            # untouched
        assert "b" in mgr.config.servers and "b" in mgr.server_names
        assert mgr.send_prompt("b", "hi", timeout=TIMEOUT)["stopReason"] == "end_turn"
        assert not mgr.is_fatal("b") and mgr.restart_count("b") == 0
        with pytest.raises(ValueError):
            mgr.add_server("b", AcpServerConfig.from_dict("b", added.server_raw(), tmp_path))
    finally:
        mgr.stop_all()


# ---------------------------------------------------------------------------
# startupTimeoutMs bounds initialize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "startup_ms, explicit, expected",
    [(120_000, None, 120.0), (10_000, None, 30.0), (120_000, 5.0, 5.0)],
    ids=["registry-budget", "default-keeps-30s-floor", "explicit-wins"],
)
def test_initialize_waits_for_the_startup_budget(agent, monkeypatch, startup_ms, explicit, expected):
    from agentao.acp_client.client import ACPClient
    from agentao.acp_client.process import ACPProcessHandle

    config = AcpServerConfig.from_dict(
        NAME, agent.server_raw(startupTimeoutMs=startup_ms), project_root=agent.dir,
    )
    client = ACPClient(ACPProcessHandle(NAME, config))
    seen = []
    monkeypatch.setattr(client, "call", lambda method, params, timeout=None: seen.append(timeout) or {})

    client.initialize(timeout=explicit)

    assert seen == [expected]


# ---------------------------------------------------------------------------
# /acp login after a successful first connect
# ---------------------------------------------------------------------------


def test_acp_login_still_runs_when_session_new_succeeds(managers, agent, piped_login, capsys):
    """Some agents ask for credentials only at session/prompt."""
    agent.cred_file.write_text("old", encoding="utf-8")
    mgr = _new(managers, agent)

    login_mod.acp_login(_cli(mgr), NAME)

    assert "reconnected" in capsys.readouterr().out
    assert len(piped_login.calls) == 1
    assert mgr.get_client(NAME).connection_info.session_id == "s-tok"


def test_acp_login_reports_no_login_needed_without_a_terminal_method(
    managers, agent, piped_login, capsys,
):
    agent.cred_file.write_text("old", encoding="utf-8")
    raw_env = {**agent.server_raw()["env"], "MOCK_NO_TERMINAL": "1"}
    mgr = _new(managers, agent, env=raw_env)

    login_mod.acp_login(_cli(mgr), NAME)

    assert "no login needed" in capsys.readouterr().out
    assert piped_login.calls == []


# ---------------------------------------------------------------------------
# add_server while another loop walks the servers
# ---------------------------------------------------------------------------


def _late_config(agent):
    return AcpServerConfig.from_dict("late", agent.server_raw(), project_root=agent.dir)


def test_get_status_survives_add_server_mid_iteration(agent, monkeypatch):
    mgr = _manager(agent, terminal_auth=False)
    original = mgr.interactions.list_pending  # read once per server in the loop

    def add_then_list(*a, **kw):
        if mgr.get_handle("late") is None:
            mgr.add_server("late", _late_config(agent))
        return original(*a, **kw)

    monkeypatch.setattr(mgr.interactions, "list_pending", add_then_list)

    names = {s.server for s in mgr.get_status()}

    assert NAME in names


def test_stop_all_and_start_all_survive_add_server_mid_iteration(agent, monkeypatch):
    mgr = _manager(agent, terminal_auth=False, autoStart=True)
    handle = mgr.get_handle(NAME)
    added = []

    def add_once(*a, **k):
        if not added:
            added.append(1)
            mgr.add_server(f"late{len(mgr.server_names)}", _late_config(agent))

    monkeypatch.setattr(handle, "start", add_once)
    mgr.start_all()
    added.clear()
    monkeypatch.setattr(handle, "stop", add_once)
    mgr.stop_all()

    assert len(mgr.server_names) == 3


# ---------------------------------------------------------------------------
# Every CLI path that builds the manager declares Terminal Auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("available", [True, False])
def test_the_explicit_route_builds_the_manager_with_terminal_auth(tmp_path, monkeypatch, available):
    from agentao.cli import acp_inbox

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(login_mod, "terminal_login_available", lambda: available)
    seen = {}

    def from_project(*a, **kw):
        seen.update(kw)
        return SimpleNamespace(server_names=[])

    monkeypatch.setattr(ACPManager, "from_project", staticmethod(from_project))
    cli = SimpleNamespace(_acp_manager=None, _acp_config_mtime=None, _acp_load_error_shown=False)

    acp_inbox.try_acp_explicit_route(cli, "@mock do something")

    assert seen == {"terminal_auth": available}


def test_registry_add_keeps_the_live_manager_across_the_explicit_route(
    tmp_path, monkeypatch, agent,
):
    from agentao.acp_client.registry import parse_registry
    from agentao.cli import acp_inbox
    from agentao.cli.commands_ext import acp_registry as registry_cli

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    index = {"agents": [{"id": "fresh", "name": "Fresh", "version": "1.0.0",
                         "distribution": {"npx": {"package": "fresh@1.0.0"}}}]}
    monkeypatch.setattr(
        "agentao.acp_client.registry.fetch_registry", lambda *a, **k: parse_registry(index),
    )
    monkeypatch.setattr(registry_cli.Confirm, "ask", staticmethod(lambda *a, **k: True))
    (project / ".agentao").mkdir()
    (project / ".agentao" / "acp.json").write_text(
        '{"servers": {"mock": %s}}' % __import__("json").dumps(agent.server_raw())
    )
    cli = SimpleNamespace(_acp_manager=None, _acp_config_mtime=None, _acp_load_error_shown=False)
    monkeypatch.setattr(login_mod, "terminal_login_available", lambda: False)
    acp_inbox.try_acp_explicit_route(cli, "@nobody hello")  # loads the manager
    live = cli._acp_manager

    registry_cli.acp_registry(cli, "add fresh")
    acp_inbox.try_acp_explicit_route(cli, "@nobody hello")

    assert cli._acp_manager is live
    assert live.get_handle("fresh") is not None


# ---------------------------------------------------------------------------
# Reservation vs. a connect already past its entry check
# ---------------------------------------------------------------------------


def _ENSURE(m):
    return m._ensure_connected_locked(NAME, timeout=TIMEOUT)


@pytest.mark.parametrize(
    "body",
    [
        lambda m: m._connect_server_locked(NAME, timeout=TIMEOUT),
        _ENSURE,
        lambda m: m._open_ephemeral_client_locked(NAME, cwd=None, mcp_servers=None, timeout=TIMEOUT),
    ],
    ids=["connect", "ensure", "ephemeral"],
)
def test_handshake_bodies_recheck_the_reservation(managers, agent, body):
    """A caller that passed the public check before the login reserved the
    server reaches these bodies under the handshake lock; they must refuse."""
    agent.cred_file.write_text("tok", encoding="utf-8")
    mgr = _new(managers, agent)
    if body is _ENSURE:
        # A cached session: the fast path that returns it without connecting.
        mgr.connect_server(NAME, timeout=TIMEOUT)
    launched = len(agent.entries("server"))
    caught = []

    def late_caller():
        try:
            with mgr._get_handshake_lock(NAME):
                body(mgr)
        except AcpClientError as exc:
            caught.append(exc)

    with mgr.reserve_for_login(NAME):
        t = threading.Thread(target=late_caller)
        t.start()
        t.join(TIMEOUT)

    assert [e.code for e in caught] == [AcpErrorCode.SERVER_BUSY]
    assert len(agent.entries("server")) == launched  # nothing launched


def test_reservation_waits_for_an_in_flight_handshake(managers, agent):
    mgr = _new(managers, agent)
    in_handshake, release, reserved = threading.Event(), threading.Event(), threading.Event()

    def in_flight_connect():
        with mgr._get_handshake_lock(NAME):
            in_handshake.set()
            release.wait(TIMEOUT)

    def login():
        with mgr.reserve_for_login(NAME):
            reserved.set()

    holder = threading.Thread(target=in_flight_connect)
    holder.start()
    in_handshake.wait(TIMEOUT)
    t = threading.Thread(target=login)
    t.start()

    assert not reserved.wait(0.5)       # blocked behind the handshake
    release.set()
    assert reserved.wait(TIMEOUT)       # then proceeds
    holder.join(TIMEOUT)
    t.join(TIMEOUT)


def test_registry_add_leaves_an_external_edit_to_reload(tmp_path, monkeypatch, agent):
    """acp.json changed under the live manager before the add: the route must
    still reload it, so the add must not mark the file as loaded."""
    import json as _json
    import os as _os

    from agentao.acp_client.registry import parse_registry
    from agentao.cli import acp_inbox
    from agentao.cli.commands_ext import acp_registry as registry_cli

    project = tmp_path / "proj"
    (project / ".agentao").mkdir(parents=True)
    monkeypatch.chdir(project)
    index = {"agents": [{"id": "fresh", "name": "Fresh", "version": "1.0.0",
                         "distribution": {"npx": {"package": "fresh@1.0.0"}}}]}
    monkeypatch.setattr(
        "agentao.acp_client.registry.fetch_registry", lambda *a, **k: parse_registry(index),
    )
    monkeypatch.setattr(registry_cli.Confirm, "ask", staticmethod(lambda *a, **k: True))
    monkeypatch.setattr(login_mod, "terminal_login_available", lambda: False)
    path = project / ".agentao" / "acp.json"
    path.write_text(_json.dumps({"servers": {"mock": agent.server_raw()}}))
    cli = SimpleNamespace(_acp_manager=None, _acp_config_mtime=None, _acp_load_error_shown=False)
    acp_inbox.try_acp_explicit_route(cli, "@nobody hello")
    stale = cli._acp_manager

    data = _json.loads(path.read_text())
    data["servers"]["external"] = agent.server_raw()
    path.write_text(_json.dumps(data))
    st = path.stat()
    _os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # a distinct mtime

    registry_cli.acp_registry(cli, "add fresh")
    acp_inbox.try_acp_explicit_route(cli, "@nobody hello")

    assert cli._acp_manager is not stale
    assert {"mock", "external", "fresh"} <= set(cli._acp_manager.server_names)


def test_a_manager_built_by_an_acp_command_survives_the_explicit_route(tmp_path, monkeypatch, agent):
    """`/acp start` then `@server …` must not swap the manager (and orphan
    what it started) just because the command path never recorded the mtime."""
    import json as _json

    from agentao.cli import acp_inbox
    from agentao.cli.commands_ext.acp import _ensure_acp_manager

    project = tmp_path / "proj"
    (project / ".agentao").mkdir(parents=True)
    (project / ".agentao" / "acp.json").write_text(_json.dumps({"servers": {"mock": agent.server_raw()}}))
    monkeypatch.chdir(project)
    monkeypatch.setattr(login_mod, "terminal_login_available", lambda: False)
    cli = SimpleNamespace(_acp_manager=None, _acp_config_mtime=None, _acp_load_error_shown=False)

    mgr = _ensure_acp_manager(cli)
    acp_inbox.try_acp_explicit_route(cli, "@nobody hello")

    assert cli._acp_manager is mgr


def test_agent_supplied_method_text_is_sanitized_on_screen(managers, agent, capsys, monkeypatch):
    from agentao.cli.commands_ext.acp import _print_auth_required

    mgr = _new(managers, agent)
    exc = AcpRpcError(rpc_code=AUTH_REQUIRED_RPC_CODE, rpc_message="auth")
    exc.details["auth_methods"] = [{"id": "x", "type": "oauth\x1b[2J‮"}]

    assert _print_auth_required(mgr, NAME, exc)

    out = capsys.readouterr().out
    assert "oauth" in out and "\x1b" not in out and "‮" not in out


def test_choosing_among_terminal_methods_shows_only_numbers_to_the_prompt(monkeypatch, capsys):
    hostile = [
        {"id": "a‮gol", "name": "First", "type": "terminal"},
        {"id": "b\x1b[2J", "name": "Second⁦", "type": "terminal"},
    ]
    asked = {}

    def ask(prompt, *, choices, default):
        asked.update(prompt=prompt, choices=choices, default=default)
        return "2"

    monkeypatch.setattr(login_mod.Prompt, "ask", staticmethod(ask))

    chosen = login_mod._choose_method(NAME, hostile, "")

    assert chosen is hostile[1]
    assert asked == {"prompt": "Method", "choices": ["1", "2"], "default": "1"}
    out = capsys.readouterr().out
    assert "1." in out and "2." in out
    assert not any(c in out for c in ("\x1b", "‮", "⁦"))
