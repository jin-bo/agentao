"""Tests for McpTool name handling and schema adaptation."""

from unittest.mock import Mock

import pytest

from agentao.mcp.tool import (
    McpTool,
    _sanitize_name,
    make_mcp_tool_name,
    parse_mcp_tool_name,
)


# ---------------------------------------------------------------------------
# _sanitize_name
# ---------------------------------------------------------------------------

def test_sanitize_name_replaces_hyphens():
    assert _sanitize_name("my-tool") == "my_tool"


def test_sanitize_name_replaces_spaces():
    assert _sanitize_name("my tool") == "my_tool"


def test_sanitize_name_replaces_dots():
    assert _sanitize_name("my.tool") == "my_tool"


def test_sanitize_name_keeps_underscores():
    assert _sanitize_name("my_tool") == "my_tool"


def test_sanitize_name_keeps_alphanumeric():
    assert _sanitize_name("myTool123") == "myTool123"


def test_sanitize_name_empty_string():
    assert _sanitize_name("") == ""


# ---------------------------------------------------------------------------
# make_mcp_tool_name
# ---------------------------------------------------------------------------

def test_make_mcp_tool_name_format():
    assert make_mcp_tool_name("github", "create_issue") == "mcp_github_create_issue"


def test_make_mcp_tool_name_sanitizes_server():
    assert make_mcp_tool_name("my-server", "list") == "mcp_my_server_list"


def test_make_mcp_tool_name_sanitizes_tool():
    assert make_mcp_tool_name("server", "get-item") == "mcp_server_get_item"


# ---------------------------------------------------------------------------
# parse_mcp_tool_name
# ---------------------------------------------------------------------------

def test_parse_mcp_tool_name_valid():
    server, tool = parse_mcp_tool_name("mcp_github_create_issue")
    assert server == "github"
    assert tool == "create_issue"


def test_parse_mcp_tool_name_underscore_in_tool():
    server, tool = parse_mcp_tool_name("mcp_myserver_do_something_complex")
    assert server == "myserver"
    assert tool == "do_something_complex"


def test_parse_mcp_tool_name_no_underscore_after_prefix():
    # "mcp_" + rest with no underscore → both return rest
    server, tool = parse_mcp_tool_name("mcp_onlyone")
    assert server == "onlyone"
    assert tool == "onlyone"


def test_parse_mcp_tool_name_invalid_prefix():
    with pytest.raises(ValueError, match="Not an MCP tool name"):
        parse_mcp_tool_name("notmcp_something")


# ---------------------------------------------------------------------------
# McpTool
# ---------------------------------------------------------------------------

def _make_mcp_tool_def(name="list_repos", description="List repos", schema=None, annotations=None):
    """Build a **real** ``mcp.types.Tool``, not a mock.

    A ``MagicMock`` answers ``hasattr`` for every name, which makes it
    actively wrong for testing code that probes across the mcp 1.x/2.x
    field-naming split (``inputSchema`` → ``input_schema``): the mock would
    satisfy the 2.x branch on a 1.x SDK and hand back a child mock instead of
    the schema. Constructing the real model with the camelCase spec names —
    field names on 1.x, aliases on 2.x — exercises the same object production
    gets, on either major.

    ``annotations`` accepts ``None`` (server provided none), a real
    ``ToolAnnotations``, or a dict we convert to one so production-realistic
    ``model_dump`` paths are exercised.
    """
    from mcp.types import Tool as McpToolDef, ToolAnnotations

    if isinstance(annotations, dict):
        annotations = ToolAnnotations(**annotations)
    return McpToolDef(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
        annotations=annotations,
    )


def test_mcptool_name_property():
    t = McpTool("github", _make_mcp_tool_def("list_repos"), call_fn=Mock())
    assert t.name == "mcp_github_list_repos"


def test_mcptool_description_includes_server():
    t = McpTool("github", _make_mcp_tool_def(description="Lists repos"), call_fn=Mock())
    assert "github" in t.description
    assert "Lists repos" in t.description


def test_mcptool_description_fallback_when_none():
    tool_def = _make_mcp_tool_def()
    tool_def.description = None
    t = McpTool("github", tool_def, call_fn=Mock())
    assert "github" in t.description


def test_mcptool_parameters_schema_forwarded():
    schema = {"type": "object", "properties": {"repo": {"type": "string"}}}
    t = McpTool("github", _make_mcp_tool_def(schema=schema), call_fn=Mock())
    assert t.parameters == schema


def test_mcptool_parameters_adds_type_when_missing():
    schema = {"properties": {"foo": {"type": "string"}}}
    t = McpTool("github", _make_mcp_tool_def(schema=schema), call_fn=Mock())
    assert t.parameters["type"] == "object"


def test_mcptool_parameters_handles_non_dict_schema():
    """``parameters`` is defensive against a schema the model would reject.

    Built with ``model_construct`` because the real ``Tool`` validates
    ``inputSchema`` as an object — the branch under test only fires for a
    server whose payload got past (or around) that validation, so the fixture
    has to bypass it too. The field name differs across SDK majors, so it is
    read off the model rather than hardcoded.
    """
    from mcp.types import Tool as McpToolDef

    key = "input_schema" if "input_schema" in McpToolDef.model_fields else "inputSchema"
    tool_def = McpToolDef.model_construct(
        name="list_repos", description="List repos", **{key: "invalid"}
    )
    t = McpTool("github", tool_def, call_fn=Mock())
    assert t.parameters == {"type": "object", "properties": {}}


def test_mcptool_execute_calls_call_fn():
    call_fn = Mock(return_value="result")
    tool_def = _make_mcp_tool_def("list_repos")
    t = McpTool("github", tool_def, call_fn=call_fn)
    result = t.execute(owner="octocat")
    call_fn.assert_called_once_with("github", "list_repos", {"owner": "octocat"})
    assert result == "result"


def test_mcptool_execute_returns_string():
    call_fn = Mock(return_value="ok")
    t = McpTool("srv", _make_mcp_tool_def(), call_fn=call_fn)
    assert isinstance(t.execute(), str)


# ---------------------------------------------------------------------------
# MCP annotation hints (readOnlyHint / destructiveHint)
# ---------------------------------------------------------------------------

def test_mcp_annotations_empty_when_none():
    t = McpTool("srv", _make_mcp_tool_def(annotations=None), call_fn=Mock())
    assert t.mcp_annotations == {}


def test_mcp_annotations_exposed_as_dict():
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"readOnlyHint": True, "title": "X"}),
        call_fn=Mock(),
    )
    ann = t.mcp_annotations
    assert ann["readOnlyHint"] is True
    assert ann["title"] == "X"


def test_read_only_hint_ignored_for_untrusted_server():
    """Spec: never make tool-use decisions on annotations from untrusted servers."""
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"readOnlyHint": True}),
        call_fn=Mock(),
        trusted=False,
    )
    assert t.is_read_only is False
    assert t.requires_confirmation is True


def test_read_only_hint_honored_when_trusted():
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"readOnlyHint": True}),
        call_fn=Mock(),
        trusted=True,
    )
    assert t.is_read_only is True
    assert t.requires_confirmation is False  # trusted + read-only


def test_destructive_hint_overrides_trust():
    """Trusted server flagging an op as destructive should still prompt.

    This is the security-positive direction the spec allows: hints can
    add friction but must never remove it on the untrusted path.
    """
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"destructiveHint": True}),
        call_fn=Mock(),
        trusted=True,
    )
    assert t.requires_confirmation is True


def test_destructive_hint_blocks_is_read_only_even_with_read_only_hint():
    """A contradictory annotation pair (both ``readOnlyHint=true`` and
    ``destructiveHint=true``) must not classify the tool as read-only.

    Otherwise the read-only-mode gate in the runner would let the call
    through and only ask for confirmation later — even though the
    server itself flagged the op as destructive.
    """
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={
            "readOnlyHint": True,
            "destructiveHint": True,
        }),
        call_fn=Mock(),
        trusted=True,
    )
    assert t.is_read_only is False
    assert t.requires_confirmation is True


def test_destructive_hint_ignored_for_untrusted_is_already_confirming():
    """Untrusted servers always require confirmation regardless of hints."""
    t = McpTool(
        "srv",
        _make_mcp_tool_def(annotations={"destructiveHint": False}),
        call_fn=Mock(),
        trusted=False,
    )
    # destructiveHint=False from an untrusted server should NOT downgrade
    # confirmation — that would be the spec violation we're guarding against.
    assert t.requires_confirmation is True


def test_no_hints_falls_back_to_trust_default():
    """With no annotations the legacy trusted/untrusted contract holds."""
    t_untrusted = McpTool("srv", _make_mcp_tool_def(annotations=None), call_fn=Mock(), trusted=False)
    t_trusted = McpTool("srv", _make_mcp_tool_def(annotations=None), call_fn=Mock(), trusted=True)
    assert t_untrusted.requires_confirmation is True
    assert t_trusted.requires_confirmation is False
    assert t_untrusted.is_read_only is False
    assert t_trusted.is_read_only is False


# ---------------------------------------------------------------------------
# Length cap, non-ASCII names, and collisions
# ---------------------------------------------------------------------------

def test_a_name_that_fits_is_unchanged():
    # Existing permission rules name these; they must keep matching.
    assert make_mcp_tool_name("my-server", "get.item") == "mcp_my_server_get_item"


def test_a_long_name_is_capped_with_a_stable_hash():
    server, tool = "github_enterprise", "list_repository_collaborators_with_permissions"
    name = make_mcp_tool_name(server, tool)
    assert len(name) == 64
    assert name.startswith("mcp_github_enterprise_list_repository_")
    # Same inputs, same name — never dependent on discovery order.
    assert make_mcp_tool_name(server, tool) == name
    # A different tool sharing the 55-character prefix gets a different name.
    assert make_mcp_tool_name(server, tool + "_v2") != name


def test_non_ascii_names_do_not_collapse_together():
    a = make_mcp_tool_name("cn", "查询")
    b = make_mcp_tool_name("cn", "搜索")
    assert a != b
    assert a.startswith("mcp_cn___") and len(a) <= 64
    assert all(c.isascii() for c in a)


class _Manager:
    """Duck-typed manager: ``register_mcp_tools`` takes the plain path."""

    clients: dict = {}

    def __init__(self, tools):
        self._tools = tools

    def get_all_tools(self):
        from mcp.types import Tool as McpToolDef

        return [
            (server, McpToolDef(name=name, inputSchema={"type": "object"}))
            for server, name in self._tools
        ]

    def get_client(self, name):
        return None

    def call_tool(self, server, tool, args):
        return f"{server}/{tool}"


def _agent():
    import logging
    from types import SimpleNamespace

    from agentao.tools.base import ToolRegistry

    return SimpleNamespace(
        tools=ToolRegistry(), _working_directory=None, filesystem=None, shell=None,
        _disable_tools=frozenset(), llm=SimpleNamespace(logger=logging.getLogger("t")),
    )


def test_a_colliding_tool_is_refused_and_the_first_keeps_the_name(caplog):
    from agentao.tooling.mcp_tools import register_mcp_tools

    agent = _agent()
    with caplog.at_level("ERROR", logger="t"):
        register_mcp_tools(agent, _Manager([("my-srv", "x"), ("my_srv", "x")]))
    tool = agent.tools.tools["mcp_my_srv_x"]
    assert tool.execute() == "my-srv/x"
    assert "already taken" in caplog.text and "'my_srv'" in caplog.text


def test_registering_the_same_tools_again_replaces_them():
    # A re-init over a new manager (a plugin adding servers) is not a collision.
    from agentao.tooling.mcp_tools import register_mcp_tools

    agent = _agent()
    register_mcp_tools(agent, _Manager([("s", "x")]))
    register_mcp_tools(agent, _Manager([("s", "x")]))
    assert list(agent.tools.tools) == ["mcp_s_x"]
