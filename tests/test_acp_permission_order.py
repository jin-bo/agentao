"""Permission prompts refer to an already opened ACP tool call."""

import io
import json
import threading

from agentao.acp.models import AcpSessionState
from agentao.acp.server import AcpServer, _PendingRequest
from agentao.acp.transport import ACPTransport
from agentao.transport.events import AgentEvent, EventType


def _setup(*, allow=True):
    output = io.StringIO()
    server = AcpServer(stdin=io.StringIO(), stdout=output)
    server.sessions.create(AcpSessionState(session_id="s"))
    transport = ACPTransport(server, "s")

    def reply(method, params):
        server.write_notification(method, params)  # preserve wire ordering
        pending = _PendingRequest("test")
        pending.result = {"outcome": {"outcome": "selected", "optionId": (
            "allow_once" if allow else "reject_once"
        )}}
        pending.event.set()
        return pending

    server.call = reply
    return transport, output


def _messages(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_permission_opens_same_call_before_request_and_start_updates_it():
    transport, output = _setup()
    transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "run_shell_command", "call_id": "c1", "args": {"command": "pwd"},
    }))
    assert transport.confirm_tool("run_shell_command", "shell", {"command": "pwd"})
    transport.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "run_shell_command", "call_id": "c1", "args": {"command": "pwd"},
    }))
    transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "run_shell_command", "call_id": "c1", "status": "ok",
    }))
    messages = _messages(output)
    assert [m["method"] for m in messages] == [
        "session/update", "session/request_permission", "session/update", "session/update",
    ]
    updates = [m["params"]["update"] for m in messages if m["method"] == "session/update"]
    assert [u["sessionUpdate"] for u in updates] == [
        "tool_call", "tool_call_update", "tool_call_update",
    ]
    assert all(u["toolCallId"] == "c1" for u in updates)
    assert messages[1]["params"]["toolCall"]["toolCallId"] == "c1"


def test_parallel_confirmations_do_not_exchange_ids():
    transport, output = _setup()
    barrier = threading.Barrier(2)

    def confirm(call_id):
        transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
            "tool": "read_file", "call_id": call_id, "args": {"path": call_id},
        }))
        barrier.wait(timeout=5)
        transport.confirm_tool("read_file", "read", {"path": call_id})

    threads = [threading.Thread(target=confirm, args=(cid,)) for cid in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    requests = [m["params"]["toolCall"] for m in _messages(output)
                if m["method"] == "session/request_permission"]
    assert {r["rawInput"]["path"]: r["toolCallId"] for r in requests} == {"a": "a", "b": "b"}


def test_same_named_calls_keep_distinct_ids():
    transport, output = _setup()
    for call_id in ("a", "b"):
        transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
            "tool": "read_file", "call_id": call_id, "args": {"path": call_id},
        }))
        transport.confirm_tool("read_file", "read", {"path": call_id})
        transport.emit(AgentEvent(EventType.TOOL_START, {
            "tool": "read_file", "call_id": call_id, "args": {"path": call_id},
        }))
        transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
            "tool": "read_file", "call_id": call_id, "status": "ok",
        }))
    openings = [m["params"]["update"]["toolCallId"] for m in _messages(output)
                if m["method"] == "session/update"
                and m["params"]["update"]["sessionUpdate"] == "tool_call"]
    assert openings == ["a", "b"]


def test_rejected_call_closes_the_opening_as_failed():
    transport, output = _setup(allow=False)
    transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "run_shell_command", "call_id": "denied", "args": {},
    }))
    assert not transport.confirm_tool("run_shell_command", "shell", {})
    transport.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "run_shell_command", "call_id": "denied", "args": {},
    }))
    transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "run_shell_command", "call_id": "denied", "status": "cancelled",
    }))
    updates = [m["params"]["update"] for m in _messages(output)
               if m["method"] == "session/update"]
    assert [u["sessionUpdate"] for u in updates].count("tool_call") == 1
    assert updates[-1]["toolCallId"] == "denied"
    assert updates[-1]["status"] == "failed"


def test_cached_permission_does_not_leak_id_to_next_direct_request():
    transport, output = _setup()
    transport._server.sessions.require("s").permission_overrides["read_file"] = True
    transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "read_file", "call_id": "cached", "args": {},
    }))
    assert transport.confirm_tool("read_file", "", {})
    assert transport.confirm_tool("run_shell_command", "", {})
    request = [m for m in _messages(output)
               if m["method"] == "session/request_permission"][0]
    assert request["params"]["toolCall"]["toolCallId"] != "cached"


def test_start_update_restates_title_and_clears_permission_description():
    # The request's ToolCallUpdate carries the tool description as content and
    # the confirm label as title; the execution update must put both back.
    transport, output = _setup()
    transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "[r 1/15] read_file", "call_id": "c", "args": {"path": "x"},
    }))
    transport.confirm_tool("[r] read_file", "Reads a file.", {"path": "x"})
    transport.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "[r 1/15] read_file", "call_id": "c", "args": {"path": "x"},
    }))
    update = _messages(output)[-1]["params"]["update"]
    assert update["sessionUpdate"] == "tool_call_update"
    assert update["title"] == "[r 1/15] read_file"
    assert update["content"] == []
    # A rejected call also gets TOOL_START, so this update must not claim
    # the call is running; it stays "pending" until output or completion.
    assert "status" not in update


def test_confirmed_todo_write_still_surfaces_as_a_plan():
    transport, output = _setup()
    args = {"todos": [{"content": "a", "status": "pending"}]}
    transport.emit(AgentEvent(EventType.TOOL_CONFIRMATION, {
        "tool": "todo_write", "call_id": "t", "args": args,
    }))
    transport.confirm_tool("todo_write", "", args)
    transport.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "todo_write", "call_id": "t", "args": args,
    }))
    transport.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "todo_write", "call_id": "t", "status": "ok",
    }))
    messages = _messages(output)
    assert messages[0]["method"] == "session/request_permission"
    assert messages[0]["params"]["toolCall"]["toolCallId"] == "t"
    updates = [m["params"]["update"]["sessionUpdate"] for m in messages
               if m["method"] == "session/update"]
    assert updates == ["plan"]
