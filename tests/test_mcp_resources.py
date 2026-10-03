"""MCP resources (docs/design/mcp-resources.md).

Against a real stdio server (``tests/support/resource_mcp_server.py``) over a
real ``ClientSession`` — no ``MagicMock`` anywhere a wire shape is asserted.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from agentao.mcp import resources as res
from agentao.mcp.client import McpClientManager
from agentao.mcp.resource_tools import MCP_RESOURCE_TOOL_NAMES
from agentao.mcp.resources import McpResourceError, render_blob
from agentao.runtime.tool_result_formatter import _prune_tool_outputs
from agentao.tooling.registry import MCP_RESOURCE_TOOL_NAMES as REGISTRY_NAMES
from tests.support.resource_mcp_server import PNG, methods, resource_server, started


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _manager(configs):
    manager = McpClientManager(configs)
    manager.connect_all()
    return manager


@pytest.fixture
def one(tmp_path):
    """A connected manager with one resource server ``docs``; yields (manager, marks)."""
    config, marks = resource_server(tmp_path / "docs")
    manager = _manager({"docs": config})
    try:
        yield manager, marks
    finally:
        manager.disconnect_all(timeout=0)


def _agent(tmp_path, manager, **kwargs):
    from agentao.agent import Agentao

    wd = tmp_path / "wd"
    wd.mkdir(exist_ok=True)
    return Agentao(
        working_directory=wd,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test.mcp_resources"),
        mcp_manager=manager,
        **kwargs,
    )


def _saved_path(text: str) -> Path:
    return Path(text.rsplit(" saved to ", 1)[1].rstrip("]"))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_registry_names_match_the_tool_module():
    assert REGISTRY_NAMES == MCP_RESOURCE_TOOL_NAMES


# ---------------------------------------------------------------------------
# Capability retention and the manager API
# ---------------------------------------------------------------------------


def test_capabilities_are_kept_and_nothing_is_listed_at_connect(one):
    manager, marks = one
    client = manager.get_client("docs")
    assert client.supports_resources is True
    assert not [m for m in methods(marks) if m.startswith("resources/")]
    status = manager.get_server_status()[0]
    assert status["resources"] is True and status["resources_enabled"] is True


def test_one_page_with_cursor(one):
    manager, _ = one
    page = manager.list_resources("docs")
    assert [r.uri for r in page.resources] == ["note://a", "ui://widget"]
    assert page.next_cursor == "p2"
    page2 = manager.list_resources("docs", page.next_cursor)
    assert [r.uri for r in page2.resources] == ["bin://img.png"]
    assert page2.next_cursor is None
    first = page.resources[0]
    assert (first.server, first.title, first.mime_type, first.size) == (
        "docs", "Note A", "text/plain", 5,
    )


def test_templates_and_method_not_found_means_none(one):
    manager, marks = one
    page = manager.list_resource_templates("docs")
    assert [t.uri_template for t in page.templates] == ["note://{id}"]
    (marks / "no-templates").touch()
    assert manager.list_resource_templates("docs").templates == []


def test_unknown_server_is_refused(one):
    manager, _ = one
    with pytest.raises(McpResourceError) as e:
        manager.read_resource("nope", "note://a")
    assert e.value.kind == "unknown_server"


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


def test_tools_register_bound_to_the_working_directory(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager)
    for name in MCP_RESOURCE_TOOL_NAMES:
        tool = agent.tools.get(name)
        assert tool.working_directory == agent.working_directory
        assert agent.tools.origin(name) == "mcp"
        assert tool.is_read_only is True
        assert tool.requires_confirmation is False
    # MCP tools get the same binding now.
    assert agent.tools.get("mcp_docs_link").working_directory == agent.working_directory


def test_list_without_server_walks_every_page_and_filters(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager)
    out = json.loads(agent.tools.get("list_mcp_resources").execute())
    assert [r["uri"] for r in out["resources"]] == ["note://a", "bin://img.png"]  # ui:// filtered
    note = out["resources"][0]
    assert note == {
        "server": "docs", "uri": "note://a", "name": "a", "title": "Note A",
        "mimeType": "text/plain", "size": 5,
    }  # _meta, icons, annotations dropped
    assert "errors" not in out and "nextCursor" not in out


def test_list_with_server_is_one_page(tmp_path, one):
    manager, _ = one
    tool = _agent(tmp_path, manager).tools.get("list_mcp_resources")
    out = json.loads(tool.execute(server="docs"))
    assert out["server"] == "docs" and out["nextCursor"] == "p2"
    out2 = json.loads(tool.execute(server="docs", cursor="p2"))
    assert [r["uri"] for r in out2["resources"]] == ["bin://img.png"]


def test_cursor_without_server_is_refused(tmp_path, one):
    manager, marks = one
    out = _agent(tmp_path, manager).tools.get("list_mcp_resources").execute(cursor="p2")
    assert out.startswith("Error: cursor requires server")


def test_templates_tool(tmp_path, one):
    manager, _ = one
    out = json.loads(
        _agent(tmp_path, manager).tools.get("list_mcp_resource_templates").execute()
    )
    assert out["resourceTemplates"] == [
        {"server": "docs", "uriTemplate": "note://{id}", "name": "note", "mimeType": "text/plain"}
    ]


def test_all_servers_sorted_with_one_failure_in_errors(tmp_path):
    a, _ = resource_server(tmp_path / "a")
    b, b_marks = resource_server(tmp_path / "b")
    manager = _manager({"zeta": a, "alpha": b})
    try:
        (b_marks / "fail-list").touch()
        agent = _agent(tmp_path, manager)
        out = json.loads(agent.tools.get("list_mcp_resources").execute())
        assert {r["server"] for r in out["resources"]} == {"zeta"}
        assert [e["server"] for e in out["errors"]] == ["alpha"]
        assert "listing broke" in out["errors"][0]["error"]
    finally:
        manager.disconnect_all(timeout=0)


def test_read_text_and_multi(tmp_path, one):
    manager, _ = one
    tool = _agent(tmp_path, manager).tools.get("read_mcp_resource")
    assert tool.execute(server="docs", uri="note://a") == "hello"
    assert tool.execute(server="docs", uri="multi://x") == (
        "--- multi://x/1 ---\none\n\n--- multi://x/2 ---\ntwo"
    )


def test_read_textual_blob_is_decoded(tmp_path, one):
    manager, _ = one
    tool = _agent(tmp_path, manager).tools.get("read_mcp_resource")
    assert tool.execute(server="docs", uri="json://data") == '{"k": 1}'


def test_read_binary_blob_is_saved_0600_under_tool_outputs(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager)
    out = agent.tools.get("read_mcp_resource").execute(server="docs", uri="bin://img.png")
    assert out.startswith("[Binary resource bin://img.png (image/png, 24 bytes) saved to ")
    path = _saved_path(out)
    assert path.parent == agent.working_directory / ".agentao" / "tool-outputs"
    assert path.name.startswith("mcp-resource_") and path.suffix == ".png"
    assert path.read_bytes() == PNG
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("bad64://x", "Error: the server returned malformed base64 for bad64://x"),
        ("badutf://x", "is typed text/plain but is not valid UTF-8"),
        ("empty://x", "The server returned no content for empty://x"),
    ],
)
def test_read_explicit_errors(tmp_path, one, uri, expected):
    manager, _ = one
    out = _agent(tmp_path, manager).tools.get("read_mcp_resource").execute(server="docs", uri=uri)
    assert expected in out


@pytest.mark.parametrize("uri", ["missing://x", "old-missing://x"])
def test_not_found_both_codes(tmp_path, one, uri):
    manager, _ = one
    with pytest.raises(McpResourceError) as e:
        manager.read_resource("docs", uri)
    assert e.value.kind == "not_found"
    out = _agent(tmp_path, manager).tools.get("read_mcp_resource").execute(server="docs", uri=uri)
    assert "Resource not found on MCP server 'docs'" in out
    assert 'list_mcp_resources(server="docs")' in out


def test_https_uri_goes_to_the_server(tmp_path, one, monkeypatch):
    import socket

    def no_network(*a, **k):
        raise AssertionError("agentao made an outbound connection")

    manager, marks = one
    monkeypatch.setattr(socket, "create_connection", no_network)
    out = manager.read_resource("docs", "https://example.invalid/doc")
    assert out.contents[0].text == "via server"
    assert methods(marks)[-1] == "resources/read"


def test_no_working_directory_saves_nothing(tmp_path, one, monkeypatch):
    from agentao.mcp.resource_tools import ReadMcpResourceTool

    manager, _ = one
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    out = ReadMcpResourceTool(manager).execute(server="docs", uri="bin://img.png")
    assert "not saved: this session has no working directory" in out
    assert list(cwd.rglob("*")) == []


def test_two_agents_sharing_a_manager_save_into_their_own_directories(tmp_path, one):
    manager, _ = one
    paths = []
    for name in ("one", "two"):
        sub = tmp_path / name
        sub.mkdir()
        agent = _agent(sub, manager)
        out = agent.tools.get("read_mcp_resource").execute(server="docs", uri="bin://img.png")
        paths.append((_saved_path(out), agent.working_directory))
    for path, wd in paths:
        assert wd in path.parents


# ---------------------------------------------------------------------------
# Size cap, before any decode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mime", ["image/png", "text/plain"])
def test_oversize_blob_is_never_decoded(monkeypatch, tmp_path, mime):
    def boom(*a, **k):
        raise AssertionError("decoded an oversize blob")

    monkeypatch.setattr(base64, "b64decode", boom)
    saved = []
    b64 = "A" * (((res.MAX_BLOB_BYTES // 3) + 2) * 4)
    out = render_blob("big://x", mime, b64, lambda *a: saved.append(a))
    assert "not decoded: over the 10.0 MiB limit" in out
    assert saved == []


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


def _age(path: Path, days: int = 8) -> None:
    old = time.time() - days * 24 * 3600
    os.utime(path, (old, old))


def test_prune_matches_saved_resources(tmp_path):
    stale = tmp_path / "mcp-resource_1_abc.png"
    fresh = tmp_path / "mcp-resource_2_def.png"
    other = tmp_path / "keep.png"
    for p in (stale, fresh, other):
        p.write_bytes(b"x")
    _age(stale)
    _age(other)
    assert _prune_tool_outputs(tmp_path) == 1
    assert not stale.exists() and fresh.exists() and other.exists()


def test_a_binary_save_prunes_without_any_text_spill(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager)
    out_dir = agent.working_directory / ".agentao" / "tool-outputs"
    out_dir.mkdir(parents=True)
    stale_bin = out_dir / "mcp-resource_1_aaa.png"
    stale_txt = out_dir / "read_file_1_bbb.txt"
    for p in (stale_bin, stale_txt):
        p.write_bytes(b"x")
        _age(p)
    agent.tools.get("read_mcp_resource").execute(server="docs", uri="bin://img.png")
    assert not stale_bin.exists() and not stale_txt.exists()


# ---------------------------------------------------------------------------
# "resources": false, missing capability, disable_tools / enabled_tools
# ---------------------------------------------------------------------------


def test_disabled_server_is_refused_before_any_request(tmp_path):
    config, marks = resource_server(tmp_path / "docs", resources=False)
    manager = _manager({"docs": config})
    try:
        for call in (
            lambda: manager.list_resources("docs"),
            lambda: manager.list_resource_templates("docs"),
            lambda: manager.read_resource("docs", "note://a"),
        ):
            with pytest.raises(McpResourceError) as e:
                call()
            assert e.value.kind == "disabled"
            assert str(e.value) == "resources are disabled for server 'docs'"
        assert manager.resource_servers() == []
        assert not [m for m in methods(marks) if m.startswith("resources/")]
        # No server qualifies, so the tools are not registered at all.
        agent = _agent(tmp_path, manager)
        assert not (MCP_RESOURCE_TOOL_NAMES & set(agent.tools.tools))
        assert "Read it with" not in agent.tools.get("mcp_docs_link").execute()
    finally:
        manager.disconnect_all(timeout=0)


def test_disabled_server_is_skipped_by_listings_and_refused_by_name(tmp_path):
    on, _ = resource_server(tmp_path / "on")
    off, off_marks = resource_server(tmp_path / "off", resources=False)
    manager = _manager({"on": on, "off": off})
    try:
        agent = _agent(tmp_path, manager)
        out = json.loads(agent.tools.get("list_mcp_resources").execute())
        assert {r["server"] for r in out["resources"]} == {"on"}
        assert "errors" not in out
        read = agent.tools.get("read_mcp_resource").execute(server="off", uri="note://a")
        assert read == "Error: resources are disabled for server 'off'"
        assert "Read it with" not in agent.tools.get("mcp_off_link").execute()
        assert not [m for m in methods(off_marks) if m.startswith("resources/")]
    finally:
        manager.disconnect_all(timeout=0)


def test_server_without_the_capability(tmp_path):
    config, marks = resource_server(tmp_path / "docs")
    (marks / "no-resources").touch()
    manager = _manager({"docs": config})
    try:
        with pytest.raises(McpResourceError) as e:
            manager.read_resource("docs", "note://a")
        assert e.value.kind == "unsupported"
        assert not [m for m in methods(marks) if m.startswith("resources/")]
        agent = _agent(tmp_path, manager)
        assert not (MCP_RESOURCE_TOOL_NAMES & set(agent.tools.tools))
    finally:
        manager.disconnect_all(timeout=0)


def test_disable_tools_skips_one_and_keeps_the_others(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager, disable_tools={"read_mcp_resource"})
    assert "read_mcp_resource" not in agent.tools.tools
    assert {"list_mcp_resources", "list_mcp_resource_templates"} <= set(agent.tools.tools)
    # No read hint: the model could not follow it.
    assert "Read it with" not in agent.tools.get("mcp_docs_link").execute()


def test_enabled_tools_accepts_the_names_with_no_resource_server(tmp_path):
    from agentao.agent import Agentao

    wd = tmp_path / "wd"
    wd.mkdir()
    agent = Agentao(
        working_directory=wd, api_key="k", base_url="https://test.local/v1", model="m",
        logger=logging.getLogger("test.mcp_resources"),
        enabled_tools={"read_mcp_resource"},
    )
    assert "read_mcp_resource" not in agent.tools.tools


def test_enabled_tools_prunes_the_other_two(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager, enabled_tools={"read_mcp_resource"})
    assert "read_mcp_resource" in agent.tools.tools
    assert "list_mcp_resources" not in agent.tools.tools
    assert "list_mcp_resource_templates" not in agent.tools.tools


def test_builtin_names_stay_pinned():
    from agentao.tooling.registry import BUILTIN_TOOL_NAMES

    assert not (BUILTIN_TOOL_NAMES & MCP_RESOURCE_TOOL_NAMES)


# ---------------------------------------------------------------------------
# Recovery before the capability check
# ---------------------------------------------------------------------------


def test_a_dropped_session_is_recovered_for_a_read(one):
    manager, marks = one
    assert len(started(marks)) == 1
    (marks / "drop-once").touch()
    read = manager.read_resource("docs", "note://a")
    assert read.contents[0].text == "hello"
    assert len(started(marks)) == 2


def test_a_disconnected_server_reconnects_then_lists(tmp_path, one):
    manager, marks = one
    client = manager.get_client("docs")
    manager._run(client.disconnect())
    assert client.supports_resources is False  # reset with the session
    page = manager.list_resources("docs")
    assert page.resources
    assert len(started(marks)) == 2
    # Still walked by an all-servers listing: it declared resources before.
    assert manager.resource_servers() == ["docs"]


def test_disabled_server_is_not_reconnected(tmp_path):
    config, marks = resource_server(tmp_path / "docs", resources=False)
    manager = _manager({"docs": config})
    try:
        manager._run(manager.get_client("docs").disconnect())
        with pytest.raises(McpResourceError):
            manager.read_resource("docs", "note://a")
        assert len(started(marks)) == 1
    finally:
        manager.disconnect_all(timeout=0)


def test_reconnected_session_without_the_capability_is_refused(one):
    manager, marks = one
    manager._run(manager.get_client("docs").disconnect())
    (marks / "no-resources").touch()
    with pytest.raises(McpResourceError) as e:
        manager.read_resource("docs", "note://a")
    assert e.value.kind == "unsupported"
    assert len(started(marks)) == 2
    assert not [m for m in methods(marks) if m.startswith("resources/")]


# ---------------------------------------------------------------------------
# Tool results that mention resources (§6)
# ---------------------------------------------------------------------------


def test_resource_link_keeps_its_uri_and_names_the_read_tool(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager)
    out = agent.tools.get("mcp_docs_link").execute()
    assert out == (
        '[Resource report://q3 "Q3 report" (application/pdf, 2.0 KiB): The quarterly report. '
        'Read it with read_mcp_resource(server="docs", uri="report://q3")]'
    )


def test_call_tool_keeps_returning_str_without_a_hint(one):
    manager, _ = one
    out = manager.call_tool("docs", "link", {})
    assert isinstance(out, str)
    assert out.startswith("[Resource report://q3") and "Read it with" not in out
    embedded = manager.call_tool("docs", "embed", {})
    assert "not saved: this session has no working directory" in embedded


def test_embedded_blob_is_saved_into_the_calling_tools_directory(tmp_path, one):
    manager, _ = one
    agent = _agent(tmp_path, manager)
    out = agent.tools.get("mcp_docs_embed").execute()
    path = _saved_path(out)
    assert agent.working_directory in path.parents
    assert path.read_bytes() == PNG


# ---------------------------------------------------------------------------
# /mcp resources
# ---------------------------------------------------------------------------


def _cli_output(manager, args, capsys):
    from types import SimpleNamespace

    from agentao.cli.commands.mcp import handle_mcp_command

    handle_mcp_command(SimpleNamespace(agent=SimpleNamespace(mcp_manager=manager)), args)
    return capsys.readouterr().out


def test_cli_lists_one_and_all_servers(tmp_path, capsys):
    a, _ = resource_server(tmp_path / "a")
    b, b_marks = resource_server(tmp_path / "b")
    off, _ = resource_server(tmp_path / "off", resources=False)
    manager = _manager({"a": a, "b": b, "off": off})
    try:
        out = _cli_output(manager, "resources a", capsys)
        assert "note://a" in out and "note://{id}" in out and "ui://widget" not in out
        (b_marks / "fail-list").touch()
        out = _cli_output(manager, "resources", capsys)
        assert "note://a" in out
        assert "b: " in out and "listing broke" in out
        assert "off: resources disabled by config" in out
        out = _cli_output(manager, "resources off", capsys)
        assert "disabled by config" in out
        out = _cli_output(manager, "list", capsys)
        assert "resources" in out
    finally:
        manager.disconnect_all(timeout=0)


def test_cli_server_without_resources(tmp_path, capsys):
    config, marks = resource_server(tmp_path / "docs")
    (marks / "no-resources").touch()
    manager = _manager({"docs": config})
    try:
        assert "declares no resources" in _cli_output(manager, "resources docs", capsys)
    finally:
        manager.disconnect_all(timeout=0)


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits")
def test_saved_file_is_not_world_readable(tmp_path):
    path = res.save_binary(tmp_path / "out", b"x", "a://b", None)
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_allowed_in_read_only_mode_while_a_write_is_denied(tmp_path, one):
    from agentao.runtime.tool_planning import ToolCallDecision

    manager, _ = one
    agent = _agent(tmp_path, manager)
    planner = agent.tool_runner._planner
    for name in MCP_RESOURCE_TOOL_NAMES:
        decision, _ = planner._decide(agent.tools.get(name), name, {}, readonly_mode=True)
        assert decision is ToolCallDecision.ALLOW, name
    write = agent.tools.get("write_file")
    decision, _ = planner._decide(write, "write_file", {}, readonly_mode=True)
    assert decision is ToolCallDecision.DENY


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


def test_a_transport_timeout_with_no_request_timeout_is_a_resource_error(one, monkeypatch):
    # With no ``timeout.request`` configured, a ``TimeoutError`` raised by the
    # transport used to reach an f-string formatting ``None`` and escape as a
    # ``TypeError``.
    manager, _ = one
    client = manager.get_client("docs")

    async def times_out(*a, **k):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(client._session, "read_resource", times_out)
    with pytest.raises(McpResourceError) as e:
        manager.read_resource("docs", "note://a")
    assert "read timed out" in str(e.value)


def test_concurrent_first_calls_share_one_client(tmp_path, monkeypatch):
    import threading

    import agentao.mcp.client as client_module

    # Widen the check-then-store window so a get-then-set races every time:
    # each thread is still inside the constructor when the others look.
    class SlowClient(client_module.McpClient):
        def __init__(self, *a, **k):
            time.sleep(0.2)
            super().__init__(*a, **k)

    monkeypatch.setattr(client_module, "McpClient", SlowClient)
    config, marks = resource_server(tmp_path / "docs")
    manager = McpClientManager({"docs": config})  # never connected: no client yet
    try:
        barrier = threading.Barrier(4)
        clients = []

        def first_call():
            barrier.wait()
            clients.append(manager._resource_client("docs"))

        threads = [threading.Thread(target=first_call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len({id(c) for c in clients}) == 1
        assert manager.get_client("docs") is clients[0]
    finally:
        manager.disconnect_all(timeout=0)


@pytest.mark.parametrize(
    "tools, hinted",
    [("mcp_docs_link", False), ("mcp_docs_link, read_mcp_resource", True)],
)
def test_a_sub_agent_is_hinted_only_when_it_can_read(tmp_path, one, monkeypatch, tools, hinted):
    """The parent's MCP tool checks the *parent's* registry; a child that does
    not get ``read_mcp_resource`` must not be pointed at it."""
    from agentao.agent import Agentao
    from openai.types.chat import ChatCompletionMessageToolCall

    manager, _ = one
    wd = tmp_path / "wd"
    agents_dir = wd / ".agentao" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reader.md").write_text(
        f"---\nname: reader\ndescription: reads\ntools: {tools}\n---\nRead things.\n"
    )
    parent = _agent(tmp_path, manager)
    results = {}

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        call = ChatCompletionMessageToolCall(
            id="c1", type="function", function={"name": "mcp_docs_link", "arguments": "{}"},
        )
        _, messages = self.tool_runner.execute([call])
        results["link"] = messages[0]["content"]
        return ""

    monkeypatch.setattr(Agentao, "chat", chat)
    try:
        parent.tools.tools["agent_reader"]._run_sync("x")
        # The parent's own instance keeps its hint.
        assert "Read it with" in parent.tools.get("mcp_docs_link").execute()
    finally:
        parent.close()
    assert results["link"].startswith("[Resource report://q3")
    assert ("Read it with read_mcp_resource" in results["link"]) is hinted


def test_a_resource_only_server_connects_and_registers_the_tools(tmp_path):
    config, marks = resource_server(tmp_path / "docs")
    (marks / "resources-only").touch()
    manager = _manager({"docs": config})
    try:
        status = manager.get_server_status()[0]
        assert status["status"] == "connected" and status["tools"] == 0
        assert manager.read_resource("docs", "note://a").contents[0].text == "hello"
        agent = _agent(tmp_path, manager)
        assert MCP_RESOURCE_TOOL_NAMES <= set(agent.tools.tools)
    finally:
        manager.disconnect_all(timeout=0)


def test_a_server_that_declares_tools_and_refuses_the_listing_still_fails(tmp_path):
    config, marks = resource_server(tmp_path / "docs")
    (marks / "tools-refused").touch()
    manager = McpClientManager({"docs": config})
    try:
        manager.connect_all()
        assert manager.get_server_status()[0]["status"] == "error"
    finally:
        manager.disconnect_all(timeout=0)
