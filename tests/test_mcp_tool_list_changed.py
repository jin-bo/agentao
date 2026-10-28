"""``"refresh_tools": true``: follow an MCP server's ``tools/list_changed``.

Against a real stdio server (``tests/support/changing_tools_mcp_server.py``)
that changes its catalogue when its ``change`` tool is called, on whichever
SDK major is installed. Design: ``docs/design/mcp-tool-list-changed.md``.
"""

from __future__ import annotations

import json
import time
from typing import Any, List
from unittest.mock import Mock

import mcp.types
import pytest
from openai.types.chat import ChatCompletion

from agentao.llm.client import LLMClient
from agentao.mcp import _compat
from agentao.mcp._compat import is_tool_list_changed
from agentao.tooling.mcp_tools import refresh_mcp_tools

from tests.support.changing_tools_mcp_server import changing_tools_server, methods, tool
from tests.support.tools import make_dummy_agent


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


def _agent(tmp_path, servers):
    return make_dummy_agent(tmp_path, extra_mcp_servers=servers)


def _mcp_names(agent, server="srv") -> List[str]:
    return sorted(n for n in agent.tools.tools if n.startswith(f"mcp_{server}_"))


def _change(agent, tools, *, notices=1, server="srv"):
    agent.mcp_manager.call_tool(server, "change", {"tools": tools, "notices": notices})


def _wait_stale(agent, server="srv", timeout=5.0) -> bool:
    client = agent.mcp_manager.get_client(server)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.tools_stale:
            return True
        time.sleep(0.01)
    return False


def _list_count(marks) -> int:
    return methods(marks).count("tools/list")


# ---------------------------------------------------------------------------
# The notification
# ---------------------------------------------------------------------------


def _as_session_hands_it(notification):
    # mcp 1.x wraps every server notification in a ``ServerNotification``
    # RootModel; 2.x hands the notification over as it is.
    wrapper = _compat._NOTIFICATION_WRAPPER
    return wrapper(notification) if wrapper is not None else notification


def test_the_notification_is_recognised_in_the_installed_sdks_shape():
    changed = _as_session_hands_it(
        mcp.types.ToolListChangedNotification(method="notifications/tools/list_changed")
    )
    other = _as_session_hands_it(
        mcp.types.ResourceListChangedNotification(
            method="notifications/resources/list_changed"
        )
    )
    assert is_tool_list_changed(changed) is True
    assert is_tool_list_changed(other) is False
    assert is_tool_list_changed(ValueError("transport")) is False
    assert is_tool_list_changed(Mock()) is False


# ---------------------------------------------------------------------------
# Opted in
# ---------------------------------------------------------------------------


def test_a_changed_list_replaces_the_servers_tools(tmp_path):
    config, marks = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        assert _mcp_names(agent) == ["mcp_srv_alpha", "mcp_srv_change"]
        _change(agent, [tool("beta")])
        assert _wait_stale(agent)
        assert refresh_mcp_tools(agent) is True
        assert _mcp_names(agent) == ["mcp_srv_beta", "mcp_srv_change"]
        assert agent.tools.origin("mcp_srv_beta") == "mcp"
        # Nothing stale any more: no request, no change.
        assert refresh_mcp_tools(agent) is False
        assert _list_count(marks) == 2
    finally:
        agent.close()


def test_many_notices_cost_one_relist(tmp_path):
    config, marks = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        _change(agent, [tool("beta")], notices=5)
        _change(agent, [tool("gamma")], notices=5)
        assert _wait_stale(agent)
        time.sleep(0.1)  # let every notice land
        assert refresh_mcp_tools(agent) is True
        assert _mcp_names(agent) == ["mcp_srv_change", "mcp_srv_gamma"]
        assert _list_count(marks) == 2  # the handshake's, and one refresh
    finally:
        agent.close()


def test_an_unchanged_list_leaves_the_registry_alone(tmp_path):
    config, marks = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        before = agent.tools.get("mcp_srv_alpha")
        _change(agent, [tool("alpha")])
        assert _wait_stale(agent)
        assert refresh_mcp_tools(agent) is False
        assert agent.tools.get("mcp_srv_alpha") is before
        assert _list_count(marks) == 2
    finally:
        agent.close()


def test_a_changed_description_is_a_change(tmp_path):
    config, _ = changing_tools_server(tmp_path, [tool("alpha", "old")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        _change(agent, [tool("alpha", "new")])
        assert _wait_stale(agent)
        assert refresh_mcp_tools(agent) is True
        assert "new" in agent.tools.get("mcp_srv_alpha").description
    finally:
        agent.close()


def test_a_failed_relist_keeps_the_tools(tmp_path):
    config, marks = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        (marks / "list_error").touch()
        _change(agent, [tool("beta")])
        assert _wait_stale(agent)
        assert refresh_mcp_tools(agent) is False
        assert _mcp_names(agent) == ["mcp_srv_alpha", "mcp_srv_change"]
        # Not retried on every request: the next notice asks again.
        assert agent.mcp_manager.get_client("srv").tools_stale is False
        (marks / "list_error").unlink()
        _change(agent, [tool("beta")])
        assert _wait_stale(agent)
        assert refresh_mcp_tools(agent) is True
        assert _mcp_names(agent) == ["mcp_srv_beta", "mcp_srv_change"]
    finally:
        agent.close()


def test_another_servers_tools_are_untouched(tmp_path):
    one, _ = changing_tools_server(tmp_path / "one", [tool("alpha")], refresh_tools=True)
    two, _ = changing_tools_server(tmp_path / "two", [tool("omega")], refresh_tools=True)
    agent = _agent(tmp_path, {"one": one, "two": two})
    try:
        kept = agent.tools.get("mcp_two_omega")
        _change(agent, [tool("beta")], server="one")
        assert _wait_stale(agent, "one")
        assert refresh_mcp_tools(agent) is True
        assert _mcp_names(agent, "one") == ["mcp_one_beta", "mcp_one_change"]
        assert agent.tools.get("mcp_two_omega") is kept
    finally:
        agent.close()


def test_a_name_another_server_holds_is_refused_on_refresh(tmp_path):
    # ``a-b``'s ``x`` and ``a_b``'s ``x`` both map to ``mcp_a_b_x``.
    first, _ = changing_tools_server(tmp_path / "one", [tool("x")], refresh_tools=True)
    second, _ = changing_tools_server(tmp_path / "two", [tool("y")], refresh_tools=True)
    agent = _agent(tmp_path, {"a-b": first, "a_b": second})
    try:
        held = agent.tools.get("mcp_a_b_x")
        _change(agent, [tool("x")], server="a_b")
        assert _wait_stale(agent, "a_b")
        refresh_mcp_tools(agent)
        assert agent.tools.get("mcp_a_b_x") is held
    finally:
        agent.close()


def test_a_reconnect_marks_the_list_stale(tmp_path):
    config, marks = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        client = agent.mcp_manager.get_client("srv")
        assert client.tools_stale is False
        agent.mcp_manager._run(client.disconnect())
        agent.mcp_manager._run(client.connect())
        assert client.tools_stale is True
        refresh_mcp_tools(agent)
        assert client.tools_stale is False
        # The reconnect's handshake listed the tools; no second ``tools/list``.
        assert _list_count(marks) == 2
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Not opted in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, False, "true", 1], ids=["absent", "false", "str", "int"])
def test_without_the_opt_in_a_notice_is_ignored(tmp_path, value):
    extra = {} if value is None else {"refresh_tools": value}
    config, marks = changing_tools_server(tmp_path, [tool("alpha")], **extra)
    agent = _agent(tmp_path, {"srv": config})
    try:
        _change(agent, [tool("beta")])
        time.sleep(0.2)
        assert agent.mcp_manager.get_client("srv").tools_stale is False
        assert refresh_mcp_tools(agent) is False
        assert _mcp_names(agent) == ["mcp_srv_alpha", "mcp_srv_change"]
        assert _list_count(marks) == 1
    finally:
        agent.close()


def test_a_hosts_own_manager_is_never_refreshed(tmp_path):
    manager = Mock()
    manager.get_all_tools.return_value = []
    agent = make_dummy_agent(tmp_path, mcp_manager=manager)
    try:
        assert refresh_mcp_tools(agent) is False
        manager.refresh_stale_tools.assert_not_called()
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Through a turn
# ---------------------------------------------------------------------------


def _call(name: str, arguments: dict, call_id: str = "c1") -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "r", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{
            "index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def _answer(text: str) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "r", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": text},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def test_the_next_request_in_the_same_turn_carries_the_new_tools(tmp_path, monkeypatch):
    config, _ = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    sent: List[List[str]] = []
    script: List[Any] = [
        _call("mcp_srv_change", {"tools": [tool("beta")]}),
        _call("mcp_srv_beta", {}, call_id="c2"),
        _answer("done"),
    ]

    # No wait between the call and the next request: the server sends the
    # notice before the call's answer, and the session's receive loop handles
    # them in order, so the flag is set by the time ``call_tool`` returns.
    def chat_stream(self, **kwargs):
        sent.append(sorted(t["function"]["name"] for t in kwargs.get("tools") or []))
        return script.pop(0)

    monkeypatch.setattr(LLMClient, "chat_stream", chat_stream)
    try:
        assert agent.chat("go") == "done"
    finally:
        agent.close()

    assert "mcp_srv_alpha" in sent[0] and "mcp_srv_beta" not in sent[0]
    # The very next request, in the same turn.
    assert "mcp_srv_beta" in sent[1] and "mcp_srv_alpha" not in sent[1]
    results = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert any("beta" in r for r in results)


def test_doctor_flags_a_non_boolean_opt_in():
    from agentao.cli.diagnostics.collectors import _validate_mcp_server_fields

    messages = _validate_mcp_server_fields({
        "a": {"command": "x", "refresh_tools": "true"},
        "b": {"command": "x", "refresh_tools": True},
        "c": {"command": "x", "skills": 1},
    })
    assert messages == [
        "server 'a': 'refresh_tools' must be true or false",
        "server 'c': 'skills' must be true or false",
    ]


def test_a_drop_right_after_the_relist_keeps_the_new_tools(tmp_path):
    """The registry takes the list the re-list returned. Read back later, a
    dropped connection's list is empty and would unregister every tool."""
    config, _ = changing_tools_server(tmp_path, [tool("alpha")], refresh_tools=True)
    agent = _agent(tmp_path, {"srv": config})
    try:
        client = agent.mcp_manager.get_client("srv")
        real_refresh = client.refresh_tools

        async def refresh_then_drop():
            listed = await real_refresh()
            await client.disconnect()
            return listed

        client.refresh_tools = refresh_then_drop
        _change(agent, [tool("beta")])
        assert _wait_stale(agent)
        assert refresh_mcp_tools(agent) is True
        assert client.tools == []
        assert _mcp_names(agent) == ["mcp_srv_beta", "mcp_srv_change"]
    finally:
        agent.close()


def test_a_tool_refused_for_a_collision_does_not_count_as_a_change(tmp_path):
    first, _ = changing_tools_server(tmp_path / "one", [tool("x")], refresh_tools=True)
    second, _ = changing_tools_server(tmp_path / "two", [tool("x")], refresh_tools=True)
    agent = _agent(tmp_path, {"a-b": first, "a_b": second})
    try:
        _change(agent, [tool("x")], server="a_b")
        assert _wait_stale(agent, "a_b")
        assert refresh_mcp_tools(agent) is False
    finally:
        agent.close()


@pytest.mark.parametrize("opted_in", [True, False], ids=["refresh_tools", "default"])
def test_a_first_success_after_a_failed_connect_is_stale_only_when_opted_in(tmp_path, opted_in):
    """The server that needed login at startup: its first connect failed, so
    the registry holds none of its tools when a later connect succeeds."""
    from agentao.mcp.client import McpClientManager

    extra = {"refresh_tools": True} if opted_in else {}
    config, _ = changing_tools_server(tmp_path, [tool("alpha")], **extra)
    script = tmp_path / "changing_tools_server.py"
    body = script.read_text()
    script.write_text("import sys; sys.exit(1)\n")  # the first connect fails
    manager = McpClientManager({"srv": config})
    try:
        manager.connect_all()
        client = manager.get_client("srv")
        assert client.status.value != "connected"
        script.write_text(body)
        manager._run(client.connect())
        assert client.status.value == "connected"
        assert client.tools_stale is opted_in
        listed = manager.refresh_stale_tools()
        assert ("srv" in listed) is opted_in
    finally:
        manager.disconnect_all()
