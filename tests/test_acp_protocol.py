"""Unit tests for the ACP stdio JSON-RPC server dispatcher.

These tests exercise ``AcpServer`` with in-memory ``StringIO`` streams so we
get full coverage of framing, dispatch, and error mapping without spawning
subprocesses or touching real stdio.

Scenarios covered:

- Valid request → registered handler → success response
- Unknown method → -32601 method-not-found
- Malformed JSON → -32700 parse error (with id=null)
- Non-object JSON → -32600 invalid request
- Missing ``jsonrpc: "2.0"`` → -32600 invalid request
- Missing ``method`` → -32600 invalid request
- Notification (no ``id``) → no response written even on error
- Handler raising ``TypeError`` → -32602 invalid params
- Handler raising generic exception → -32603 internal error
- Multiple requests in one run → ordered responses
"""

import io
import json

import pytest

from agentao.acp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
)
from agentao.acp.server import AcpServer


def _run(server: AcpServer, stdin: io.StringIO, stdout: io.StringIO) -> list[dict]:
    """Run the server against in-memory streams and parse NDJSON output."""
    server.run()
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _make_server(input_text: str) -> tuple[AcpServer, io.StringIO, io.StringIO]:
    stdin = io.StringIO(input_text)
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    return server, stdin, stdout


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_registered_handler_success():
    server, stdin, stdout = _make_server(
        '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"msg":"hi"}}\n'
    )
    server.register("ping", lambda params: {"echo": params["msg"]})

    responses = _run(server, stdin, stdout)

    assert len(responses) == 1
    assert responses[0] == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"echo": "hi"},
    }


def test_handler_returning_none_still_serializes_result():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","id":"abc","method":"noop"}\n')
    server.register("noop", lambda _params: None)

    responses = _run(server, stdin, stdout)

    assert len(responses) == 1
    assert responses[0]["id"] == "abc"
    assert "result" in responses[0]
    assert responses[0]["result"] is None
    assert "error" not in responses[0]


# ---------------------------------------------------------------------------
# Dispatch errors
# ---------------------------------------------------------------------------

def test_unknown_method_returns_method_not_found():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","id":42,"method":"nope"}\n')

    responses = _run(server, stdin, stdout)

    assert len(responses) == 1
    assert responses[0]["id"] == 42
    assert responses[0]["error"]["code"] == METHOD_NOT_FOUND
    assert "result" not in responses[0]


def test_handler_typeerror_maps_to_invalid_params():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","id":1,"method":"strict"}\n')

    def strict(params):
        raise TypeError("expected 'x'")

    server.register("strict", strict)

    responses = _run(server, stdin, stdout)

    assert responses[0]["error"]["code"] == INVALID_PARAMS
    assert "expected 'x'" in responses[0]["error"]["message"]


def test_handler_generic_exception_maps_to_internal_error():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","id":1,"method":"boom"}\n')

    def boom(_params):
        raise RuntimeError("kapow")

    server.register("boom", boom)

    responses = _run(server, stdin, stdout)

    assert responses[0]["error"]["code"] == INTERNAL_ERROR
    assert "kapow" in responses[0]["error"]["message"]


# ---------------------------------------------------------------------------
# Framing / validation errors
# ---------------------------------------------------------------------------

def test_malformed_json_returns_parse_error_with_null_id():
    server, stdin, stdout = _make_server("this is not json\n")

    responses = _run(server, stdin, stdout)

    assert len(responses) == 1
    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == PARSE_ERROR


def test_non_object_json_returns_invalid_request():
    server, stdin, stdout = _make_server("[1, 2, 3]\n")

    responses = _run(server, stdin, stdout)

    assert responses[0]["error"]["code"] == INVALID_REQUEST


def test_missing_jsonrpc_version_returns_invalid_request():
    server, stdin, stdout = _make_server('{"id":1,"method":"ping"}\n')
    server.register("ping", lambda _p: "pong")

    responses = _run(server, stdin, stdout)

    assert responses[0]["id"] == 1
    assert responses[0]["error"]["code"] == INVALID_REQUEST


def test_missing_method_returns_invalid_request():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","id":1}\n')

    responses = _run(server, stdin, stdout)

    assert responses[0]["error"]["code"] == INVALID_REQUEST


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def test_notification_returns_no_response_on_success():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","method":"log","params":{"x":1}}\n')
    seen = []
    server.register("log", lambda params: seen.append(params) or "ok")

    responses = _run(server, stdin, stdout)

    assert responses == []
    assert seen == [{"x": 1}]


def test_notification_returns_no_response_even_on_unknown_method():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","method":"nope"}\n')

    responses = _run(server, stdin, stdout)

    assert responses == []


def test_notification_returns_no_response_even_when_handler_raises():
    server, stdin, stdout = _make_server('{"jsonrpc":"2.0","method":"boom"}\n')
    server.register("boom", lambda _p: (_ for _ in ()).throw(RuntimeError("nope")))

    responses = _run(server, stdin, stdout)

    assert responses == []


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def test_blank_lines_are_ignored():
    server, stdin, stdout = _make_server(
        '\n\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n'
    )
    server.register("ping", lambda _p: "pong")

    responses = _run(server, stdin, stdout)

    assert len(responses) == 1
    assert responses[0]["result"] == "pong"


def test_multiple_requests_responses_are_ordered():
    server, stdin, stdout = _make_server(
        '{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        '{"jsonrpc":"2.0","id":3,"method":"ping"}\n'
    )
    server.register("ping", lambda _p: "pong")

    responses = _run(server, stdin, stdout)

    assert [r["id"] for r in responses] == [1, 2, 3]
    assert all(r["result"] == "pong" for r in responses)


def test_write_notification_produces_valid_jsonrpc_notification():
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)

    server.write_notification("session/update", {"sessionId": "s1", "update": {"kind": "hello"}})

    line = stdout.getvalue().strip()
    payload = json.loads(line)
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "session/update"
    assert "id" not in payload
    assert payload["params"]["sessionId"] == "s1"


def test_constructor_with_explicit_streams_does_not_mutate_sys_stdout():
    import sys

    original = sys.stdout
    AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
    assert sys.stdout is original, "explicit streams must not trigger the stdout guard"
