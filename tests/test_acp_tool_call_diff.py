"""A file-editing call opens with the edit it proposes.

ACP's signature UX for an edit tool is a reviewable diff, and agentao sent
`"replace"` plus a raw argument dict — the diff view the protocol exists to
enable was the one thing the transport never used. This is the `diff` half
of G2 in ``docs/design/acp-server-conformance-review.md``.

Two things these tests hold that are easy to lose:

- The diff describes what was **requested**, at ``status: "pending"``. The
  terminal update says whether it applied. ACP's own reference adapter emits
  the same optimistic entry at tool-use time
  (``agentclientprotocol/claude-agent-acp@d571358``, ``src/diff.ts``).
- **A non-append ``write_file`` gets no diff at all**, and that is the whole
  point of the last class here rather than an omission to be tidied up later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from agentao.acp._transport_helpers import _tool_call_diff, proposed_tool_diff
from agentao.acp.protocol import METHOD_SESSION_UPDATE
from agentao.acp.schema import AcpSessionUpdateParams
from agentao.acp.transport import ACPTransport
from agentao.transport.events import AgentEvent, EventType

from .support.acp_server import RecordingServer


class _SessionStub:
    def __init__(self, cwd: Path | None) -> None:
        self.cwd = cwd


class _SessionsStub:
    def __init__(self, cwd: Path | None) -> None:
        self._session = _SessionStub(cwd)

    def require(self, _session_id: str) -> _SessionStub:
        return self._session


class _ServerWithSession(RecordingServer):
    def __init__(self, cwd: Path | None = None) -> None:
        super().__init__()
        self.sessions = _SessionsStub(cwd)


@pytest.fixture
def transport(tmp_path):
    server = _ServerWithSession(cwd=tmp_path)
    return ACPTransport(server=server, session_id="s1"), server, tmp_path


def _updates(server: RecordingServer) -> List[Dict[str, Any]]:
    out = []
    for method, params in server.notifications:
        assert method == METHOD_SESSION_UPDATE
        out.append(params["update"])
    return out


def _start(t: ACPTransport, tool: str, args: Dict[str, Any], call_id: str = "c1") -> None:
    t.emit(AgentEvent(EventType.TOOL_START, {"tool": tool, "call_id": call_id, "args": args}))


def _diffs(update: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for e in update.get("content", []) if e.get("type") == "diff"]


# ---------------------------------------------------------------------------
# replace
# ---------------------------------------------------------------------------

def test_replace_opens_with_the_hunk_it_proposes(transport):
    t, server, cwd = transport
    _start(t, "replace", {
        "file_path": "src/config.py",
        "old_text": "DEBUG = False",
        "new_text": "DEBUG = True",
    })
    update = _updates(server)[-1]
    assert update["sessionUpdate"] == "tool_call"
    assert update["status"] == "pending"
    assert update["kind"] == "edit"
    assert _diffs(update) == [{
        "type": "diff",
        "path": str(cwd / "src/config.py"),
        "oldText": "DEBUG = False",
        "newText": "DEBUG = True",
    }]


def test_an_absolute_path_is_left_alone(transport, tmp_path_factory):
    # Built from a real temp dir rather than written as "/etc/hosts": on
    # Windows a path with a root but no drive is *not* absolute, and joining
    # it onto the session cwd (giving ``C:\\etc\\hosts``) is the correct answer
    # there — so a literal POSIX path tests a different branch per platform.
    t, server, _cwd = transport
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "hosts"
    assert elsewhere.is_absolute()
    _start(t, "replace", {"file_path": str(elsewhere), "old_text": "a", "new_text": "b"})
    assert _diffs(_updates(server)[-1])[0]["path"] == str(elsewhere)


def test_with_no_session_cwd_the_path_is_passed_through_rather_than_dropped():
    """A relative path is worth more to a client than no diff at all."""
    server = _ServerWithSession(cwd=None)
    t = ACPTransport(server=server, session_id="s1")
    _start(t, "replace", {"file_path": "rel.py", "old_text": "a", "new_text": "b"})
    assert _diffs(_updates(server)[-1])[0]["path"] == "rel.py"


def test_a_delete_is_an_empty_new_side(transport):
    t, server, cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "dead_code()\n", "new_text": ""})
    assert _diffs(_updates(server)[-1])[0]["newText"] == ""


def test_an_empty_old_text_gets_no_diff(transport):
    """``EditTool`` refuses it outright, so there is no edit to propose."""
    t, server, _cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "", "new_text": "x"})
    assert _diffs(_updates(server)[-1]) == []


@pytest.mark.parametrize("args", [
    {},
    {"file_path": "a.py"},
    {"file_path": "a.py", "old_text": "x"},
    {"file_path": "", "old_text": "x", "new_text": "y"},
    {"file_path": 7, "old_text": "x", "new_text": "y"},
    {"file_path": "a.py", "old_text": "x", "new_text": None},
    {"file_path": "a.py", "old_text": ["x"], "new_text": "y"},
], ids=["empty", "path only", "no new_text", "blank path", "non-str path",
        "null new_text", "list old_text"])
def test_malformed_arguments_produce_no_diff_and_no_crash(transport, args):
    t, server, _cwd = transport
    _start(t, "replace", args)
    update = _updates(server)[-1]
    assert _diffs(update) == []
    AcpSessionUpdateParams.model_validate({"sessionId": "s1", "update": update})


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def test_an_append_is_a_pure_addition(transport):
    t, server, cwd = transport
    _start(t, "write_file", {"file_path": "log.txt", "content": "one more line\n",
                             "append": True})
    assert _diffs(_updates(server)[-1]) == [{
        "type": "diff",
        "path": str(cwd / "log.txt"),
        "oldText": None,
        "newText": "one more line\n",
    }]


def test_a_whole_file_write_gets_no_diff(transport):
    """Deliberate, and the reason matters.

    The arguments give ``newText`` but say nothing about whether the file
    exists, and the transport cannot find out — it holds no filesystem, and a
    host may have injected one that does not answer to local paths. Emitting
    ``oldText: null`` would render an overwrite as a creation: all-green, at
    exactly the moment the user is asked to approve destroying what was there.
    """
    t, server, _cwd = transport
    _start(t, "write_file", {"file_path": "config.py", "content": "NEW = 1\n"})
    update = _updates(server)[-1]
    assert _diffs(update) == []
    assert update["kind"] == "edit", "still recognisably an edit"
    assert update["rawInput"]["content"] == "NEW = 1\n", "the content is still shown"


def test_append_false_is_the_same_as_no_append(transport):
    t, server, _cwd = transport
    _start(t, "write_file", {"file_path": "a.py", "content": "x", "append": False})
    assert _diffs(_updates(server)[-1]) == []


def test_a_truthy_non_true_append_is_not_an_append():
    """``is True``, not truthiness — a stray string must not open the lane."""
    assert _tool_call_diff("write_file", {"file_path": "/a", "content": "x",
                                          "append": "yes"}, base=None) is None


def test_an_empty_append_gets_no_diff(transport):
    t, server, _cwd = transport
    _start(t, "write_file", {"file_path": "a.py", "content": "", "append": True})
    assert _diffs(_updates(server)[-1]) == []


# ---------------------------------------------------------------------------
# Everything else
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["read_file", "run_shell_command", "glob",
                                  "web_fetch", "save_memory", "mcp_x_y"])
def test_a_non_editing_tool_gets_no_diff_and_no_session_lookup(transport, tool):
    t, server, _cwd = transport
    _start(t, tool, {"file_path": "a.py", "old_text": "x", "new_text": "y"})
    assert "content" not in _updates(server)[-1]


def test_the_gate_is_checked_before_the_session_is_looked_up():
    """``proposed_tool_diff`` must not touch the server for a plain tool."""
    class _Exploding:
        @property
        def sessions(self):  # pragma: no cover - must never be reached
            raise AssertionError("session looked up for a non-editing tool")

    assert proposed_tool_diff(_Exploding(), "s1", "read_file", {"file_path": "a"}) is None


# ---------------------------------------------------------------------------
# Interaction with the streamed content collection
# ---------------------------------------------------------------------------

def test_the_diff_survives_a_later_streamed_update(transport):
    """ACP replaces the collection, so a chunk must restate the diff."""
    t, server, _cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "x", "new_text": "y"})
    t.emit(AgentEvent(EventType.TOOL_OUTPUT,
                      {"tool": "replace", "call_id": "c1", "chunk": "working\n"}))

    update = _updates(server)[-1]
    assert update["sessionUpdate"] == "tool_call_update"
    assert _diffs(update), "the diff would be dropped by a bare chunk update"
    texts = [e for e in update["content"] if e.get("type") == "content"]
    assert texts[0]["content"]["text"] == "working\n"


def test_the_opening_diff_is_not_restated_at_completion(transport):
    """The ``tool_call`` already carried it; repeating it is noise."""
    t, server, _cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "x", "new_text": "y"})
    t.emit(AgentEvent(EventType.TOOL_COMPLETE,
                      {"tool": "replace", "call_id": "c1", "status": "ok"}))

    update = _updates(server)[-1]
    assert update["status"] == "completed"
    assert "content" not in update


def test_a_failed_edit_restates_the_diff_beside_the_error(transport):
    """Replace semantics: the error alone would erase the proposed edit."""
    t, server, _cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "x", "new_text": "y"})
    t.emit(AgentEvent(EventType.TOOL_COMPLETE, {
        "tool": "replace", "call_id": "c1", "status": "error",
        "error": "Old text not found",
    }))

    update = _updates(server)[-1]
    assert update["status"] == "failed"
    assert _diffs(update), "the client must still see what was attempted"
    assert update["content"][-1]["content"]["text"] == "Error: Old text not found"


def test_the_buffer_is_released_after_an_edit(transport):
    t, _server, _cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "x", "new_text": "y"})
    assert t._tool_call_content
    t.emit(AgentEvent(EventType.TOOL_COMPLETE,
                      {"tool": "replace", "call_id": "c1", "status": "ok"}))
    assert t._tool_call_content == {}


# ---------------------------------------------------------------------------
# The confirmation dialog — the moment the diff exists for
# ---------------------------------------------------------------------------

def test_the_permission_request_leads_with_the_diff(tmp_path):
    """``session/request_permission`` is where review-before-approve happens."""
    from agentao.acp._transport_helpers import proposed_tool_diff as _p

    diff = _p(_ServerWithSession(cwd=tmp_path), "s1", "replace", {
        "file_path": "a.py", "old_text": "x", "new_text": "y",
    })
    assert diff is not None and diff["type"] == "diff"


def test_the_emitted_tool_call_validates_against_the_published_schema(transport):
    t, server, _cwd = transport
    _start(t, "replace", {"file_path": "a.py", "old_text": "x", "new_text": "y"})
    for _method, params in server.notifications:
        AcpSessionUpdateParams.model_validate(params)


# ---------------------------------------------------------------------------
# session/load — a reloaded edit looks like the edit it was
# ---------------------------------------------------------------------------

def _persisted_edit(call_id: str = "call_1", **args: Any) -> List[Dict[str, Any]]:
    import json

    args = args or {"file_path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}
    return [
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "replace", "arguments": json.dumps(args)},
        }]},
        {"role": "tool", "tool_call_id": call_id, "content": "Replaced 1 occurrence(s) in a.py"},
    ]


def test_a_replayed_edit_opens_with_its_diff(transport):
    t, server, cwd = transport
    t.replay_history(_persisted_edit())
    opening = next(u for u in _updates(server) if u["sessionUpdate"] == "tool_call")
    assert _diffs(opening) == [{
        "type": "diff", "path": str(cwd / "a.py"),
        "oldText": "x = 1", "newText": "x = 2",
    }]


def test_the_replayed_result_restates_the_diff_beside_its_text(transport):
    """Replace semantics again: the result text alone would erase the diff."""
    t, server, _cwd = transport
    t.replay_history(_persisted_edit())
    result = _updates(server)[-1]
    assert result["sessionUpdate"] == "tool_call_update"
    assert _diffs(result), "the diff would be wiped by a text-only result"
    assert result["content"][-1]["content"]["text"].startswith("Replaced 1")


def test_a_replayed_whole_file_write_still_gets_no_diff(transport):
    import json

    t, server, _cwd = transport
    t.replay_history([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "w1", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"file_path": "a.py", "content": "N"})},
        }]},
        {"role": "tool", "tool_call_id": "w1", "content": "Successfully wrote to a.py"},
    ])
    assert all(not _diffs(u) for u in _updates(server))


def test_replayed_diffs_do_not_outlive_their_load(transport):
    t, _server, _cwd = transport
    t.replay_history(_persisted_edit()[:1])      # a call with no result
    assert t._replay_diffs
    t.replay_history([])
    assert t._replay_diffs == {}


def test_replayed_updates_validate_against_the_published_schema(transport):
    t, server, _cwd = transport
    t.replay_history(_persisted_edit())
    for _method, params in server.notifications:
        AcpSessionUpdateParams.model_validate(params)
