"""ACP Terminal Auth and per-session LLM configuration (issue #380).

What a Registry install depends on, end to end through the real handlers and
the real default agent factory (no network — building an ``LLMClient`` makes
no request):

- ``initialize`` advertises the terminal login only to a client that declared
  it, in either spelling — the Registry's validator sends the legacy
  ``_meta["terminal-auth"]``, DeepChat and Brokk send ``auth.terminal``;
- ``session/new`` / ``session/load`` answer ``auth_required`` (-32000) when no
  provider is configured, and a configuration *error* keeps its own code;
- a login completed while the server runs is seen by the next ``session/new``
  on the same process (Brokk retries there instead of reconnecting), while a
  session that already exists keeps the configuration it was built with;
- two sessions from two projects never share credentials, at creation or on
  a model switch, and nothing is written into ``os.environ``.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_new as acp_session_new
from agentao.acp.initialize import handle_initialize
from agentao.acp.llm_auth import TERMINAL_AUTH_METHOD_ID, login_command
from agentao.acp.protocol import (
    ACP_PROTOCOL_VERSION,
    AUTH_REQUIRED,
    INTERNAL_ERROR,
    INVALID_REQUEST,
)
from agentao.acp.server import AcpServer, JsonRpcHandlerError
from agentao.acp.session_load import handle_session_load
from agentao.acp.session_new import handle_session_new
from agentao.acp.session_set_config_option import handle_session_set_config_option
from agentao.embedding.llm_config import save_user_llm_config

from .support.acp_agents import make_recording_factory

_TERMINAL_CAPS = {"auth": {"terminal": True}}


def _relocate_home(monkeypatch, home: Path) -> None:
    """Point every variable this platform's ``expanduser`` reads at ``home``."""
    home_s = str(home)
    monkeypatch.setenv("HOME", home_s)
    monkeypatch.setenv("USERPROFILE", home_s)
    drive, tail = os.path.splitdrive(home_s)
    if drive:
        monkeypatch.setenv("HOMEDRIVE", drive)
        monkeypatch.setenv("HOMEPATH", tail or "\\")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    _relocate_home(monkeypatch, path)
    return path


def _login(home: Path, *, provider: str, api_key: str, model: str,
           base_url: str = "https://llm.example/v1") -> None:
    save_user_llm_config(
        home / ".agentao" / "llm.json",
        provider=provider, api_key=api_key, base_url=base_url, model=model,
    )


def _project(tmp_path: Path, name: str, dotenv: str = "") -> Path:
    path = tmp_path / name
    path.mkdir()
    if dotenv:
        (path / ".env").write_text(dotenv, encoding="utf-8")
    return path


_servers: List[AcpServer] = []


@pytest.fixture(autouse=True)
def _close_sessions():
    yield
    while _servers:
        _servers.pop().sessions.close_all()


def _server(caps: Optional[Dict[str, Any]] = None,
            launch_env: Optional[Dict[str, str]] = None) -> AcpServer:
    server = AcpServer(
        stdin=io.StringIO(""), stdout=io.StringIO(), launch_env=launch_env or {},
    )
    handle_initialize(server, {
        "protocolVersion": ACP_PROTOCOL_VERSION,
        "clientCapabilities": caps if caps is not None else {},
    })
    _servers.append(server)
    return server


def _new(server: AcpServer, cwd: Path) -> Dict[str, Any]:
    return handle_session_new(server, {"cwd": str(cwd), "mcpServers": []})


def _llm(server: AcpServer, session_id: str):
    return server.sessions.get(session_id).agent.llm


# ---------------------------------------------------------------------------
# initialize: capability negotiation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "caps",
    [
        {"auth": {"terminal": True}},                        # ACP v1 (DeepChat, Brokk)
        {"_meta": {"terminal-auth": True}},                  # legacy (Registry validator)
        {"auth": {"terminal": True}, "_meta": {"terminal-auth": True}},  # Zed sends both
    ],
)
def test_terminal_login_is_advertised_for_either_capability_spelling(caps):
    result = handle_initialize(_server(), {
        "protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": caps,
    })

    (method,) = result["authMethods"]
    assert method["id"] == TERMINAL_AUTH_METHOD_ID
    assert method["type"] == "terminal"
    assert method["args"] == ["--login"]


@pytest.mark.parametrize(
    "caps",
    [
        {},
        {"terminal": True},                        # the terminal/* capability is not auth
        {"auth": {"terminal": "true"}},            # only the boolean counts
        {"auth": {"terminal": 1}},
        {"auth": True},
        {"_meta": {"terminal-auth": {"command": "x"}}},
        {"_meta": "terminal-auth"},
    ],
)
def test_no_login_is_advertised_without_a_boolean_true_declaration(caps):
    result = handle_initialize(_server(), {
        "protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": caps,
    })

    assert result["authMethods"] == []


def test_advertised_args_cannot_be_mutated_through_a_response():
    first = handle_initialize(_server(), {
        "protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": _TERMINAL_CAPS,
    })
    first["authMethods"][0]["args"].append("--oops")

    second = handle_initialize(_server(), {
        "protocolVersion": ACP_PROTOCOL_VERSION, "clientCapabilities": _TERMINAL_CAPS,
    })
    assert second["authMethods"][0]["args"] == ["--login"]


# ---------------------------------------------------------------------------
# auth_required, and what is not auth_required
# ---------------------------------------------------------------------------

def test_session_new_without_configuration_is_auth_required(home, tmp_path):
    server = _server(_TERMINAL_CAPS)

    with pytest.raises(JsonRpcHandlerError) as info:
        _new(server, _project(tmp_path, "p"))

    assert info.value.code == AUTH_REQUIRED
    assert "login" in info.value.message
    assert len(server.sessions) == 0


def test_without_terminal_auth_the_error_names_a_runnable_uvx_command(home, tmp_path):
    with pytest.raises(JsonRpcHandlerError) as info:
        _new(_server({}), _project(tmp_path, "p"))

    assert info.value.code == AUTH_REQUIRED
    assert login_command() in info.value.message
    assert login_command().startswith("uvx agentao@")


def test_session_load_without_configuration_is_auth_required(home, tmp_path):
    with pytest.raises(JsonRpcHandlerError) as info:
        handle_session_load(_server(_TERMINAL_CAPS), {
            "sessionId": "sess_whatever", "cwd": str(_project(tmp_path, "p")),
            "mcpServers": [],
        })

    assert info.value.code == AUTH_REQUIRED


def test_an_auth_failure_leaves_the_resume_directive_for_the_retry(home, tmp_path):
    from agentao.acp.models import ResumeDirective

    server = _server(_TERMINAL_CAPS)
    server.resume_directive = ResumeDirective(session_id=None)

    with pytest.raises(JsonRpcHandlerError):
        _new(server, _project(tmp_path, "p"))

    assert server.resume_directive.consume() is True


def test_a_broken_login_file_is_a_config_error_not_auth_required(home, tmp_path):
    (home / ".agentao").mkdir()
    (home / ".agentao" / "llm.json").write_text('{"api_key": "sk-SECRET-1",', encoding="utf-8")

    with pytest.raises(JsonRpcHandlerError) as info:
        _new(_server(_TERMINAL_CAPS), _project(tmp_path, "p"))

    assert info.value.code == INTERNAL_ERROR
    assert "llm.json" in info.value.message
    assert "sk-SECRET-1" not in info.value.message


def test_a_malformed_setting_is_a_config_error_not_auth_required(home, tmp_path):
    _login(home, provider="DEEPSEEK", api_key="sk-SECRET-2", model="m")
    project = _project(tmp_path, "p", "LLM_MAX_TOKENS=lots\n")

    with pytest.raises(JsonRpcHandlerError) as info:
        _new(_server(_TERMINAL_CAPS), project)

    assert info.value.code == INTERNAL_ERROR
    assert "LLM_MAX_TOKENS" in info.value.message
    assert "sk-SECRET-2" not in info.value.message


def test_a_host_injected_factory_is_neither_checked_nor_handed_a_config(home, tmp_path):
    """The host owns its credentials: no auth_required, and no new kwarg."""
    factory, calls = make_recording_factory()
    server = _server(_TERMINAL_CAPS)

    handle_session_new(
        server, {"cwd": str(_project(tmp_path, "p")), "mcpServers": []},
        agent_factory=factory,
    )

    assert "llm_config" not in calls[0]


# ---------------------------------------------------------------------------
# Login while the server runs; snapshots
# ---------------------------------------------------------------------------

def test_a_login_during_the_run_is_seen_by_the_next_session_on_the_same_process(home, tmp_path):
    server = _server(_TERMINAL_CAPS)
    project = _project(tmp_path, "p")
    with pytest.raises(JsonRpcHandlerError):
        _new(server, project)

    _login(home, provider="DEEPSEEK", api_key="ds-1", model="deepseek-chat")
    result = _new(server, project)

    llm = _llm(server, result["sessionId"])
    assert (llm.api_key, llm.model) == ("ds-1", "deepseek-chat")
    assert result["configOptions"][0]["currentValue"] == "deepseek/deepseek-chat"


def test_an_existing_session_keeps_its_configuration_after_a_new_login(home, tmp_path):
    server = _server(_TERMINAL_CAPS)
    project = _project(tmp_path, "p")
    _login(home, provider="DEEPSEEK", api_key="ds-old", model="old-model")
    first = _new(server, project)["sessionId"]

    _login(home, provider="DEEPSEEK", api_key="ds-new", model="new-model")
    second = _new(server, project)["sessionId"]

    assert _llm(server, first).api_key == "ds-old"
    assert _llm(server, second).api_key == "ds-new"

    # A provider switch on the first session resolves against its snapshot.
    handle_session_set_config_option(server, {
        "sessionId": first, "configId": "model", "value": "deepseek/other-model",
    })
    assert (_llm(server, first).api_key, _llm(server, first).model) == ("ds-old", "other-model")


# ---------------------------------------------------------------------------
# Isolation between projects in one process
# ---------------------------------------------------------------------------

def test_two_projects_in_one_process_never_share_credentials(home, tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_API_KEY", raising=False)
    monkeypatch.delenv("BETA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    environ_before = dict(os.environ)
    alpha = _project(tmp_path, "alpha", (
        "LLM_PROVIDER=ALPHA\nALPHA_API_KEY=alpha-key\n"
        "ALPHA_BASE_URL=https://alpha/v1\nALPHA_MODEL=alpha-model\n"
        "JINA_API_KEY=alpha-jina\n"
    ))
    beta = _project(tmp_path, "beta", (
        "LLM_PROVIDER=BETA\nBETA_API_KEY=beta-key\n"
        "BETA_BASE_URL=https://beta/v1\nBETA_MODEL=beta-model\n"
    ))
    server = _server({})

    a = _new(server, alpha)["sessionId"]
    b = _new(server, beta)["sessionId"]

    assert (_llm(server, a).api_key, _llm(server, a).base_url) == ("alpha-key", "https://alpha/v1")
    assert (_llm(server, b).api_key, _llm(server, b).base_url) == ("beta-key", "https://beta/v1")
    # Nothing either project's .env holds reached the shared environment —
    # LLM keys or otherwise.
    assert dict(os.environ) == environ_before

    # A model switch stays inside the session's own provider block ...
    handle_session_set_config_option(server, {
        "sessionId": b, "configId": "model", "value": "beta/beta-2",
    })
    assert (_llm(server, b).api_key, _llm(server, b).model) == ("beta-key", "beta-2")
    # ... and cannot reach the other project's.
    with pytest.raises(JsonRpcHandlerError) as info:
        handle_session_set_config_option(server, {
            "sessionId": b, "configId": "model", "value": "alpha/alpha-model",
        })
    assert info.value.code == INVALID_REQUEST
    assert _llm(server, b).api_key == "beta-key"


def test_the_launch_environment_still_configures_tools(home, tmp_path, monkeypatch):
    """Non-LLM settings come from the launch environment, unchanged."""
    monkeypatch.setenv("JINA_API_KEY", "launch-jina")
    _login(home, provider="DEEPSEEK", api_key="ds", model="m")
    project = _project(tmp_path, "p", "JINA_API_KEY=project-jina\n")

    _new(_server({}), project)

    assert os.environ["JINA_API_KEY"] == "launch-jina"


def test_the_launch_environment_outranks_the_login(home, tmp_path):
    _login(home, provider="DEEPSEEK", api_key="from-login", model="m")
    server = _server({}, launch_env={
        "DEEPSEEK_API_KEY": "from-launch", "DEEPSEEK_BASE_URL": "https://launch/v1",
    })

    session = _new(server, _project(tmp_path, "p"))["sessionId"]

    llm = _llm(server, session)
    assert (llm.api_key, llm.base_url, llm.model) == ("from-launch", "https://launch/v1", "m")


def test_a_shell_key_never_reaches_the_logins_gateway(home, tmp_path):
    """The review's case end to end: auth_required, and the key is not paired."""
    _login(home, provider="OPENAI", api_key="gateway-key", model="gpt-x",
           base_url="https://gateway.corp/v1")
    server = _server(_TERMINAL_CAPS, launch_env={"OPENAI_API_KEY": "real-openai-key"})

    with pytest.raises(JsonRpcHandlerError) as info:
        _new(server, _project(tmp_path, "p"))

    assert info.value.code == AUTH_REQUIRED
    assert "missing: base_url" in info.value.message
    assert "real-openai-key" not in info.value.message


# ---------------------------------------------------------------------------
# On the wire
# ---------------------------------------------------------------------------

def test_stdout_carries_only_protocol_messages_and_no_credentials(home, tmp_path):
    """A partial login (key but no model) is auth_required; the key never prints."""
    (home / ".agentao").mkdir()
    (home / ".agentao" / "llm.json").write_text(
        json.dumps({"provider": "DEEPSEEK", "api_key": "sk-SECRET-3"}), encoding="utf-8",
    )
    project = _project(tmp_path, "p")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {"_meta": {"terminal-auth": True}},
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": str(project), "mcpServers": []}},
    ]
    stdout = io.StringIO()
    server = AcpServer(
        stdin=io.StringIO("".join(json.dumps(m) + "\n" for m in lines)),
        stdout=stdout, launch_env={},
    )
    acp_initialize.register(server)
    acp_session_new.register(server)
    server.run()

    out = stdout.getvalue()
    messages = [json.loads(line) for line in out.splitlines()]
    assert all(m.get("jsonrpc") == "2.0" for m in messages)
    by_id = {m["id"]: m for m in messages}
    assert by_id[1]["result"]["authMethods"][0]["type"] == "terminal"
    assert by_id[2]["error"]["code"] == AUTH_REQUIRED
    assert "missing: base_url, model" in by_id[2]["error"]["message"]
    assert "sk-SECRET-3" not in out
