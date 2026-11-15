"""An untrusted MCP server's read-only marks: say why they do not count.

``McpTool.is_read_only`` honours a server's ``readOnlyHint`` only when the
server has ``"trust": true``, so read-only mode blocks the tools of an untrusted
server even when the server marks them read-only. Before this, neither the
block message nor ``/mcp list`` said so: a user saw a read-only tool blocked in
read-only mode and had to read ``mcp.json`` and the server's tool list to learn
why.

The listing tests connect a real stdio MCP subprocess; the gate tests drive
the real ``ToolRunner`` with a real ``McpTool`` built on a real
``mcp.types.Tool``.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mcp.types import Tool as McpToolDef, ToolAnnotations

from agentao import Agentao
from agentao.mcp.client import McpClientManager
from agentao.mcp.tool import McpTool, annotations_claim_read_only
from agentao.permissions import PermissionEngine, PermissionMode

from .support.stdio_mcp_server import stdio_server

_BLOCKED = "[Readonly mode]"
_TRUST_SENTENCE = 'that mark counts only for a server with "trust": true in mcp.json'


# ── what counts as a read-only mark ─────────────────────────────────────────


@pytest.mark.parametrize(
    "annotations, expected",
    [
        ({"readOnlyHint": True}, True),
        ({"readOnlyHint": True, "destructiveHint": False}, True),
        # A contradictory pair is not a read-only mark.
        ({"readOnlyHint": True, "destructiveHint": True}, False),
        ({"readOnlyHint": False}, False),
        ({}, False),
    ],
)
def test_annotations_claim_read_only(annotations, expected):
    assert annotations_claim_read_only(annotations) is expected


def _tool_def(name, annotations=None):
    return McpToolDef(
        name=name,
        description="d",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(**annotations) if annotations else None,
    )


@pytest.mark.parametrize(
    "trusted, annotations, ignored",
    [
        (False, {"readOnlyHint": True}, True),
        (True, {"readOnlyHint": True}, False),
        (False, None, False),
        (False, {"readOnlyHint": True, "destructiveHint": True}, False),
    ],
)
def test_read_only_hint_ignored(trusted, annotations, ignored):
    tool = McpTool("srv", _tool_def("t", annotations), call_fn=Mock(), trusted=trusted)
    assert tool.read_only_hint_ignored is ignored


# ── the read-only block message ─────────────────────────────────────────────


def _agent(tmp_path):
    agent = Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path,
        logger=logging.getLogger("test.mcp_untrusted_read_only_mark"),
        permission_engine=PermissionEngine(project_root=tmp_path),
    )
    agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
    agent.tool_runner.set_readonly_mode(True)
    return agent


def _run(agent, name):
    call = SimpleNamespace(
        id="call_0", type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps({})),
    )
    _, messages = agent.tool_runner.execute([call])
    return messages[0]["content"]


def test_the_block_says_the_mark_needs_trust(tmp_path):
    agent = _agent(tmp_path)
    try:
        call_fn = Mock(return_value="ran")
        tool = McpTool(
            "tavily", _tool_def("extract", {"readOnlyHint": True}),
            call_fn=call_fn, trusted=False,
        )
        agent.tools.register(tool, origin="mcp")
        result = _run(agent, tool.name)
        assert result.startswith(_BLOCKED)
        assert _TRUST_SENTENCE in result
        # The cost of the fix travels with it.
        assert "run without confirmation" in result
        call_fn.assert_not_called()
    finally:
        agent.close()


@pytest.mark.parametrize(
    "annotations",
    [None, {"readOnlyHint": True, "destructiveHint": True}],
    ids=["unmarked", "contradictory"],
)
def test_the_block_of_an_unmarked_tool_says_nothing_about_trust(tmp_path, annotations):
    # Trust would not let this tool through, so the message must not suggest it.
    agent = _agent(tmp_path)
    try:
        tool = McpTool("srv", _tool_def("write", annotations), call_fn=Mock(), trusted=False)
        agent.tools.register(tool, origin="mcp")
        result = _run(agent, tool.name)
        assert result.startswith(_BLOCKED)
        assert "trust" not in result
    finally:
        agent.close()


# ── /mcp list ───────────────────────────────────────────────────────────────


def _listing(manager):
    from agentao.cli._globals import console
    from agentao.cli.commands.mcp import handle_mcp_command

    cli = SimpleNamespace(agent=SimpleNamespace(mcp_manager=manager))
    with console.capture() as captured:
        handle_mcp_command(cli, "list")
    return captured.get()


@pytest.mark.parametrize("trusted", [False, True])
def test_mcp_list_counts_the_marks_and_says_when_they_do_not_count(tmp_path, trusted):
    config, _ = stdio_server(tmp_path, delay=0.0, read_only=True)
    config["trust"] = trusted
    manager = McpClientManager({"probe": config})
    try:
        manager.connect_all()
        status = manager.get_server_status()[0]
        listing = _listing(manager)
    finally:
        manager.disconnect_all()
    assert status["status"] == "connected"
    assert status["read_only_marked"] == 3
    listing = " ".join(listing.split())  # the console wraps long lines
    hint = '3 tool(s) marked read-only; the marks count only with "trust": true'
    assert (hint in listing) is (not trusted)
    assert ("(trusted)" in listing) is trusted


def test_mcp_list_says_nothing_for_a_server_without_marks(tmp_path):
    config, _ = stdio_server(tmp_path, delay=0.0, read_only=False)
    config["trust"] = False
    manager = McpClientManager({"probe": config})
    try:
        manager.connect_all()
        status = manager.get_server_status()[0]
        listing = _listing(manager)
    finally:
        manager.disconnect_all()
    assert status["read_only_marked"] == 0
    assert "marked read-only" not in listing
