"""ACP Registry query/conversion, the `acp.json` writer, and `/acp registry` (#381).

Fixtures copy the shapes of real entries from the live index (fetched
2026-10-01): an unscoped and a scoped npm package, ``name==X`` and
``name@X`` uv specs, a binary-only entry, and a binary+npx entry. Nothing in
this file launches an agent: every test that goes through search/add
patches ``subprocess.Popen`` to fail.
"""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from agentao.acp_client.config import add_server_entry, load_acp_client_config
from agentao.acp_client.manager import ACPManager
from agentao.acp_client.models import AcpClientConfig, AcpConfigError, ServerState
from agentao.acp_client.registry import (
    REGISTRY_STARTUP_TIMEOUT_MS,
    REGISTRY_URL,
    RegistryError,
    entry_to_server_config,
    fetch_registry,
    find_agent,
    package_version,
    parse_registry,
    search_registry,
)
from agentao.cli.commands_ext import acp_registry as registry_cli

INDEX = {
    "version": "1.0.0",
    "agents": [
        {"id": "agoragentic-acp", "name": "Agoragentic", "version": "1.3.0",
         "description": "Agent marketplace",
         "distribution": {"npx": {"package": "agoragentic-mcp@1.3.0", "args": ["--acp"]}}},
        {"id": "claude-acp", "name": "Claude Agent", "version": "0.84.0",
         "description": "ACP wrapper for Anthropic's Claude",
         "distribution": {"npx": {"package": "@agentclientprotocol/claude-agent-acp@0.84.0"}}},
        {"id": "auggie", "name": "Auggie CLI", "version": "0.36.0", "description": "Augment",
         "distribution": {"npx": {"package": "@augmentcode/auggie@0.36.0", "args": ["--acp"],
                                  "env": {"AUGMENT_DISABLE_AUTO_UPDATE": "1"}}}},
        {"id": "fast-agent", "name": "fast-agent", "version": "0.10.1", "description": "Python",
         "distribution": {"uvx": {"package": "fast-agent-acp==0.10.1", "args": ["-x"]}}},
        {"id": "minion-code", "name": "Minion Code", "version": "0.1.44", "description": "",
         "distribution": {"uvx": {"package": "minion-code@0.1.44", "args": ["acp"]}}},
        {"id": "native-only", "name": "Native", "version": "2.0.0", "description": "binary",
         "distribution": {"binary": {"darwin-aarch64": {"archive": "https://x/a.tgz", "cmd": "./a"}}}},
        {"id": "dual", "name": "Dual", "version": "1.0.0", "description": "binary and npx",
         "distribution": {"binary": {}, "npx": {"package": "dual-agent@1.0.0"}}},
        {"id": "broken"},
    ],
}


@pytest.fixture
def agents():
    return parse_registry(INDEX)


@pytest.fixture(autouse=True)
def no_launch(monkeypatch):
    """Search, conversion and add must never start a process."""
    def refuse(*a, **k):
        raise AssertionError(f"launched a process: {a!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)


# ---------------------------------------------------------------------------
# Parse / search
# ---------------------------------------------------------------------------


def test_parse_skips_malformed_entries(agents):
    assert "broken" not in [a.id for a in agents]
    assert len(agents) == 7


def test_parse_rejects_a_non_index():
    with pytest.raises(RegistryError):
        parse_registry({"agents": "nope"})


def test_search_ranks_exact_id_then_name_then_description(agents):
    assert search_registry(agents, "claude-acp")[0].id == "claude-acp"
    assert [a.id for a in search_registry(agents, "AUGMENT")] == ["auggie"]  # description, any case
    assert [a.id for a in search_registry(agents, "binary")] == ["native-only", "dual"]
    assert search_registry(agents, "zzz") == []


def test_find_agent(agents):
    assert find_agent(agents, "auggie").name == "Auggie CLI"
    with pytest.raises(RegistryError):
        find_agent(agents, "missing")


# ---------------------------------------------------------------------------
# Fetch (local transport only)
# ---------------------------------------------------------------------------


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_reads_the_index():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=INDEX)

    assert len(fetch_registry(client=_client(handler))) == 7
    assert seen == [REGISTRY_URL]


@pytest.mark.parametrize(
    "handler, message",
    [
        (lambda r: httpx.Response(503), "HTTP 503"),
        (lambda r: httpx.Response(200, content=b"<html>"), "not valid JSON"),
        (lambda r: httpx.Response(200, json={"agents": 1}), "no 'agents' list"),
    ],
)
def test_fetch_failures_are_registry_errors(handler, message):
    with pytest.raises(RegistryError, match=message):
        fetch_registry(client=_client(handler))


def test_a_network_failure_is_a_registry_error():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(RegistryError, match="could not read"):
        fetch_registry(client=_client(handler))


def test_an_oversized_index_is_refused(monkeypatch):
    import agentao.acp_client.registry as registry_mod

    monkeypatch.setattr(registry_mod, "_MAX_INDEX_BYTES", 100)
    with pytest.raises(RegistryError, match="larger than"):
        fetch_registry(client=_client(lambda r: httpx.Response(200, json=INDEX)))


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "runner, package, version",
    [
        ("npx", "agoragentic-mcp@1.3.0", "1.3.0"),
        ("npx", "@agentclientprotocol/claude-agent-acp@0.84.0", "0.84.0"),
        ("uvx", "fast-agent-acp==0.10.1", "0.10.1"),
        ("uvx", "minion-code@0.1.44", "0.1.44"),
        ("uvx", "agentao[cli]@0.5.8", "0.5.8"),
        ("uvx", "agentao[cli,web]==0.5.8", "0.5.8"),
        ("npx", "unpinned-agent", None),
        ("npx", "@scope/unpinned", None),
        ("uvx", "pkg>=1.0", None),
        ("uvx", "git+https://example.com/pkg", None),
    ],
)
def test_package_version(runner, package, version):
    assert package_version(runner, package) == version


def test_npx_entry_converts_to_a_stopped_config(agents):
    entry = entry_to_server_config(find_agent(agents, "auggie"))

    assert entry.runner == "npx"
    assert entry.config == {
        "command": "npx",
        "args": ["--yes", "@augmentcode/auggie@0.36.0", "--acp"],
        "env": {"AUGMENT_DISABLE_AUTO_UPDATE": "1"},
        "cwd": ".",
        "autoStart": False,
        "startupTimeoutMs": REGISTRY_STARTUP_TIMEOUT_MS,
        "description": "Auggie CLI 0.36.0 (ACP Registry: auggie)",
    }


def test_uvx_entry_keeps_spec_and_args(agents):
    entry = entry_to_server_config(find_agent(agents, "fast-agent"))

    assert entry.command_line == ["uvx", "fast-agent-acp==0.10.1", "-x"]


def test_binary_only_entries_are_refused(agents):
    with pytest.raises(RegistryError, match="binary; only npx and uvx"):
        entry_to_server_config(find_agent(agents, "native-only"))


def test_a_binary_plus_npx_entry_uses_npx(agents):
    assert entry_to_server_config(find_agent(agents, "dual")).runner == "npx"


def test_an_unoffered_runner_is_refused(agents):
    with pytest.raises(RegistryError, match="no uvx distribution"):
        entry_to_server_config(find_agent(agents, "auggie"), runner="uvx")


def _agent(**dist):
    return parse_registry({"agents": [
        {"id": "x", "name": "X", "version": "1.0.0", "distribution": dist},
    ]})[0]


@pytest.mark.parametrize(
    "dist, message",
    [
        ({"npx": {"package": "x@2.0.0"}}, "pins 2.0.0"),
        ({"npx": {"package": "x"}}, "unsupported npx package spec"),
        ({"npx": {}}, "has no package"),
        ({"npx": {"package": "x@1.0.0", "args": "--acp"}}, "'args' must be"),
        ({"npx": {"package": "x@1.0.0", "env": {"A": 1}}}, "'env' must"),
        ({"npx": {"package": "x@1.0.0", "env": {"HOME_DIR": "$HOME/x"}}}, "contain '\\$'"),
    ],
)
def test_entries_that_cannot_be_stored_as_written_are_refused(dist, message):
    with pytest.raises(RegistryError, match=message):
        entry_to_server_config(_agent(**dist))


# ---------------------------------------------------------------------------
# add_server_entry
# ---------------------------------------------------------------------------


def _acp_json(root):
    return root / ".agentao" / "acp.json"


def _entry(agents, agent_id="auggie"):
    return entry_to_server_config(find_agent(agents, agent_id)).config


def test_add_creates_the_file(tmp_path, agents):
    config = add_server_entry("auggie", _entry(agents), project_root=tmp_path)

    assert config.auto_start is False
    assert config.startup_timeout_ms == REGISTRY_STARTUP_TIMEOUT_MS
    assert config.cwd == str(tmp_path.resolve())
    loaded = load_acp_client_config(tmp_path)
    assert loaded.servers["auggie"].args == ["--yes", "@augmentcode/auggie@0.36.0", "--acp"]


def test_add_keeps_everything_else(tmp_path, agents):
    path = _acp_json(tmp_path)
    path.parent.mkdir()
    existing = {
        "$schema": "https://example.com/acp.schema.json",
        "servers": {"manual": {"command": "my-agent", "args": [], "env": {"TOKEN": "${TOKEN}"},
                               "cwd": "sub", "requestTimeoutMs": 5000}},
    }
    path.write_text(json.dumps(existing))

    add_server_entry("auggie", _entry(agents), project_root=tmp_path)

    written = json.loads(path.read_text())
    assert written["$schema"] == existing["$schema"]
    assert written["servers"]["manual"] == existing["servers"]["manual"]  # $-reference kept verbatim
    assert list(written["servers"]) == ["manual", "auggie"]


def test_a_name_collision_is_refused_and_nothing_changes(tmp_path, agents):
    add_server_entry("auggie", _entry(agents), project_root=tmp_path)
    before = _acp_json(tmp_path).read_bytes()

    with pytest.raises(AcpConfigError, match="already exists"):
        add_server_entry("auggie", _entry(agents, "fast-agent"), project_root=tmp_path)

    assert _acp_json(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"servers": {"bad": {"command": "x"}}}), json.dumps([1])],
)
def test_an_invalid_existing_file_is_not_overwritten(tmp_path, agents, content):
    path = _acp_json(tmp_path)
    path.parent.mkdir()
    path.write_text(content)

    with pytest.raises(AcpConfigError):
        add_server_entry("auggie", _entry(agents), project_root=tmp_path)

    assert path.read_text() == content


def test_an_invalid_new_entry_writes_nothing(tmp_path):
    with pytest.raises(AcpConfigError):
        add_server_entry("x", {"command": "x"}, project_root=tmp_path)

    assert not _acp_json(tmp_path).exists()


def test_a_failed_write_leaves_the_old_file(tmp_path, agents, monkeypatch):
    add_server_entry("auggie", _entry(agents), project_root=tmp_path)
    before = _acp_json(tmp_path).read_bytes()

    def fail(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(AcpConfigError, match="cannot write"):
        add_server_entry("fast", _entry(agents, "fast-agent"), project_root=tmp_path)

    assert _acp_json(tmp_path).read_bytes() == before
    leftovers = {p.name for p in _acp_json(tmp_path).parent.iterdir()} - {".acp.json.lock"}
    assert leftovers == {"acp.json"}  # no temp file left


@pytest.mark.parametrize("name", ["", " padded", "\t"])
def test_bad_names_are_refused(tmp_path, agents, name):
    with pytest.raises(AcpConfigError):
        add_server_entry(name, _entry(agents), project_root=tmp_path)


# ---------------------------------------------------------------------------
# /acp registry
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch, agents):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("agentao.acp_client.registry.fetch_registry", lambda *a, **k: agents)
    return tmp_path


def _answer(monkeypatch, yes):
    monkeypatch.setattr(registry_cli.Confirm, "ask", staticmethod(lambda *a, **k: yes))


def _manager_with_running(tmp_path):
    """A manager whose one server's process is a stand-in that is 'running'."""
    config = AcpClientConfig.from_dict(
        {"servers": {"existing": {"command": "x", "args": [], "env": {}, "cwd": "."}}},
        project_root=tmp_path,
    )
    mgr = ACPManager(config)
    handle = mgr.get_handle("existing")
    handle._proc = SimpleNamespace(poll=lambda: None, pid=4242)
    handle.info.pid = 4242
    handle._set_state(ServerState.READY)
    return mgr


def test_search_lists_matches(project, capsys):
    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "search claude")

    out = capsys.readouterr().out
    assert "claude-acp" in out and "0.84.0" in out and "npx" in out


def test_search_marks_unsupported_and_reports_no_match(project, capsys):
    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "search native")
    assert "unsupported" in capsys.readouterr().out

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "search zzz")
    assert "No ACP Registry agent matches" in capsys.readouterr().out


def test_add_confirms_writes_and_registers_without_starting(project, monkeypatch, capsys):
    mgr = _manager_with_running(project)
    _answer(monkeypatch, True)

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=mgr), "add auggie aug")

    out = capsys.readouterr().out
    assert "npx --yes @augmentcode/auggie@0.36.0 --acp" in out  # shown before confirming
    assert "Added 'aug'" in out
    saved = json.loads(_acp_json(project).read_text())["servers"]["aug"]
    assert saved["autoStart"] is False
    assert mgr.get_handle("aug").info.state is ServerState.CONFIGURED
    assert mgr.get_handle("existing").info.pid == 4242
    assert mgr.get_handle("existing").info.state is ServerState.READY


def test_declining_writes_nothing(project, monkeypatch, capsys):
    _answer(monkeypatch, False)

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "add auggie")

    assert "Not added" in capsys.readouterr().out
    assert not _acp_json(project).exists()


def test_add_refuses_a_name_the_manager_already_has(project, monkeypatch, capsys):
    mgr = _manager_with_running(project)
    _answer(monkeypatch, True)

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=mgr), "add auggie existing")

    assert "already configured" in capsys.readouterr().out
    assert not _acp_json(project).exists()


def test_add_refuses_a_binary_only_entry(project, monkeypatch, capsys):
    _answer(monkeypatch, True)

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "add native-only")

    assert "only npx and uvx" in capsys.readouterr().out
    assert not _acp_json(project).exists()


def test_a_saved_entry_is_loaded_by_a_later_manager(project, monkeypatch):
    _answer(monkeypatch, True)

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "add fast-agent")

    assert ACPManager.from_project(project).get_handle("fast-agent") is not None


# ---------------------------------------------------------------------------
# Registry text is third-party: no control or bidi characters reach the screen
# ---------------------------------------------------------------------------

_HOSTILE = {"agents": [{
    "id": "evil", "name": "Evil\x1b[2J‮Agent", "version": "1.0.0",
    "description": "looks fine\x1b]0;title\x07⁦",
    "distribution": {"npx": {"package": "evil@1.0.0", "args": ["--acp‮", "\x1b[31mred"]}},
}]}
_FORBIDDEN = ("\x1b", "\x07", "‮", "⁦")


@pytest.fixture
def hostile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agentao.acp_client.registry.fetch_registry", lambda *a, **k: parse_registry(_HOSTILE),
    )


def _plain_output(capsys):
    out = capsys.readouterr().out
    return out, [c for c in _FORBIDDEN if c in out]


def test_search_output_drops_control_and_bidi_characters(hostile, capsys):
    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "search evil")

    out, found = _plain_output(capsys)
    assert "evil" in out and found == []


def test_add_confirmation_drops_control_and_bidi_characters(hostile, monkeypatch, capsys):
    _answer(monkeypatch, False)

    registry_cli.acp_registry(SimpleNamespace(_acp_manager=None), "add evil")

    out, found = _plain_output(capsys)
    assert "Command:" in out and found == []


def test_a_writer_waits_for_the_config_lock(tmp_path, agents):
    """Two writers must not both read before either replaces the file."""
    import threading

    from filelock import FileLock

    add_server_entry("auggie", _entry(agents), project_root=tmp_path)
    lock = FileLock(str(_acp_json(tmp_path).parent / ".acp.json.lock"))
    done = threading.Event()
    errors = []

    def second_writer():
        try:
            add_server_entry("fast", _entry(agents, "fast-agent"), project_root=tmp_path)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        done.set()

    with lock:  # another process mid-update
        t = threading.Thread(target=second_writer)
        t.start()
        assert not done.wait(0.5)            # blocked, has not written
        assert "fast" not in json.loads(_acp_json(tmp_path).read_text())["servers"]
    t.join(10)

    assert errors == []
    assert set(json.loads(_acp_json(tmp_path).read_text())["servers"]) == {"auggie", "fast"}
